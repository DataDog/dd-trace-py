import threading

from ..vendor import monotonic


class RateLimiter(object):
    """
    A token bucket rate limiter implementation
    """
    __slots__ = ('rate_limit', 'tokens', 'max_tokens', 'last_update', '_lock')

    def __init__(self, rate_limit):
        """
        Constructor for RateLimiter

        :param rate_limit: The rate limit to apply for number of transactions per second.
            rate limit > 0 max number of transactions to allow per second,
            rate limit == 0 to disallow any transactions,
            rate limit < 0 to disable any rate limiting
        :type rate_limit: :obj:`int`
        """
        self.rate_limit = rate_limit
        self.tokens = rate_limit
        self.max_tokens = rate_limit

        self.last_update = monotonic.monotonic()
        self._lock = threading.Lock()

    def is_allowed(self):
        """
        Check whether the current transaction is allowed or not

        This method will also reduce the number of available transactions by 1

        :returns: Whether the current transaction is allowed or not
        :rtype: :obj:`bool`
        """
        # Rate limit of 0 blocks everything
        if self.rate_limit == 0:
            return False

        # Negative rate limit disables rate limiting
        elif self.rate_limit < 0:
            return True

        # Lock, we need this to be thread safe, it should be shared by all threads
        with self._lock:
            self._replenish()

            if self.tokens >= 1:
                self.tokens -= 1
                return True

            return False

    def _replenish(self):
        # If we are at the max, we do not need to add any more
        if self.tokens == self.max_tokens:
            return

        # Add more available tokens based on how much time has passed
        now = monotonic.monotonic()
        elapsed = now - self.last_update
        self.last_update = now

        # Update the number of available tokens, but ensure we do not exceed the max
        self.tokens = min(
            self.max_tokens,
            self.tokens + (elapsed * self.rate_limit),
        )

    def __repr__(self):
        return '{}(rate_limit={!r}, tokens={!r}, last_update={!r})'.format(
            self.__class__.__name__,
            self.rate_limit,
            self.tokens,
            self.last_update,
        )

    __str__ = __repr__
