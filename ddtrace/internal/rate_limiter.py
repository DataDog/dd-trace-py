from __future__ import division

import threading
from typing import Optional

from ..internal import compat


class RateLimiter(object):
    """
    A token bucket rate limiter implementation
    """

    __slots__ = (
        "_lock",
        "current_window_ns",
        "last_update_ns",
        "max_tokens",
        "prev_window_rate",
        "rate_limit",
        "tokens",
        "tokens_allowed",
        "tokens_total",
    )

    def __init__(self, rate_limit):
        # type: (int) -> None
        """
        Constructor for RateLimiter

        :param rate_limit: The rate limit to apply for number of requests per second.
            rate limit > 0 max number of requests to allow per second,
            rate limit == 0 to disallow all requests,
            rate limit < 0 to allow all requests
        :type rate_limit: :obj:`int`
        """
        self.rate_limit = rate_limit
        self.tokens = rate_limit  # type: float
        self.max_tokens = rate_limit

        self.last_update_ns = compat.monotonic_ns()

        self.current_window_ns = 0  # type: float
        self.tokens_allowed = 0
        self.tokens_total = 0
        self.prev_window_rate = None  # type: Optional[float]

        self._lock = threading.Lock()

    def is_allowed(self, timestamp_ns):
        # type: (int) -> bool
        """
        Check whether the current request is allowed or not

        This method will also reduce the number of available tokens by 1

        :param int timestamp_ns: timestamp in nanoseconds for the current request.
        :returns: Whether the current request is allowed or not
        :rtype: :obj:`bool`
        """
        # Determine if it is allowed
        allowed = self._is_allowed(timestamp_ns)
        # Update counts used to determine effective rate
        self._update_rate_counts(allowed, timestamp_ns)
        return allowed

    def _update_rate_counts(self, allowed, timestamp_ns):
        # type: (bool, int) -> None
        # No tokens have been seen yet, start a new window
        if not self.current_window_ns:
            self.current_window_ns = timestamp_ns

        # If more than 1 second has past since last window, reset
        # DEV: We are comparing nanoseconds, so 1e9 is 1 second
        elif timestamp_ns - self.current_window_ns >= 1e9:
            # Store previous window's rate to average with current for `.effective_rate`
            self.prev_window_rate = self._current_window_rate()
            self.tokens_allowed = 0
            self.tokens_total = 0
            self.current_window_ns = timestamp_ns

        # Keep track of total tokens seen vs allowed
        if allowed:
            self.tokens_allowed += 1
        self.tokens_total += 1

    def _is_allowed(self, timestamp_ns):
        # type: (int) -> bool
        # Rate limit of 0 blocks everything
        if self.rate_limit == 0:
            return False

        # Negative rate limit disables rate limiting
        elif self.rate_limit < 0:
            return True

        # Lock, we need this to be thread safe, it should be shared by all threads
        with self._lock:
            self._replenish(timestamp_ns)

            if self.tokens >= 1:
                self.tokens -= 1
                return True

            return False

    def _replenish(self, timestamp_ns):
        # type: (int) -> None
        # If we are at the max, we do not need to add any more
        if self.tokens == self.max_tokens:
            return

        # Add more available tokens based on how much time has passed
        # DEV: We store as nanoseconds, convert to seconds
        elapsed = (timestamp_ns - self.last_update_ns) / 1e9
        self.last_update_ns = timestamp_ns

        # Update the number of available tokens, but ensure we do not exceed the max
        self.tokens = min(
            self.max_tokens,
            self.tokens + (elapsed * self.rate_limit),
        )

    def _current_window_rate(self):
        # type: () -> float
        # No tokens have been seen, effectively 100% sample rate
        # DEV: This is to avoid division by zero error
        if not self.tokens_total:
            return 1.0

        # Get rate of tokens allowed
        return self.tokens_allowed / self.tokens_total

    @property
    def effective_rate(self):
        # type: () -> float
        """
        Return the effective sample rate of this rate limiter

        :returns: Effective sample rate value 0.0 <= rate <= 1.0
        :rtype: :obj:`float``
        """
        # If we have not had a previous window yet, return current rate
        if self.prev_window_rate is None:
            return self._current_window_rate()

        return (self._current_window_rate() + self.prev_window_rate) / 2.0

    def __repr__(self):
        return "{}(rate_limit={!r}, tokens={!r}, last_update_ns={!r}, effective_rate={!r})".format(
            self.__class__.__name__,
            self.rate_limit,
            self.tokens,
            self.last_update_ns,
            self.effective_rate,
        )

    __str__ = __repr__
