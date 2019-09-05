import threading

from ..vendor import monotonic


class RateLimiter(object):
    """
    A token bucket rate limiter implementation
    """
    __slots__ = (
        '_lock',
        'last_update',
        'max_tokens',
        'rate_limit',
        'tokens',
        'current_window',
        'tokens_allowed',
        'tokens_total',
    )

    def __init__(self, rate_limit):
        """
        Constructor for RateLimiter

        :param rate_limit: The rate limit to apply for number of requests per second.
            rate limit > 0 max number of requests to allow per second,
            rate limit == 0 to disallow all requests,
            rate limit < 0 to allow all requests
        :type rate_limit: :obj:`int`
        """
        self.rate_limit = rate_limit
        self.tokens = rate_limit
        self.max_tokens = rate_limit

        self.last_update = monotonic.monotonic()
        self.current_window = self.last_update
        self.tokens_allowed = 0
        self.tokens_total = 0

        self._lock = threading.Lock()

    def is_allowed(self):
        """
        Check whether the current request is allowed or not

        This method will also reduce the number of available tokens by 1

        :returns: Whether the current request is allowed or not
        :rtype: :obj:`bool`
        """
        # Determine if it is allowed
        allowed = self._is_allowed()

        # Keep track of total tokens seen and allowed in any 1s window
        now = monotonic.monotonic()
        if now - self.current_window > 1.0:
            self.tokens_allowed = 0
            self.tokens_total = 0
            self.current_window = now

        if allowed:
            self.tokens_allowed += 1
        self.tokens_total += 1

        return allowed

    def _is_allowed(self):
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

    @property
    def effective_rate(self):
        """
        Return the effective sample rate of this rate limiter

        The value is determined from the total number of tokens seen vs allowed
        in a 1s rolling window

        :returns: Effective sample rate value 0.0 <= rate <= 1.0
        :rtype: :obj:`float``
        """
        # (tokens allowed) / (total tokens seen)
        return self.tokens_allowed / self.tokens_seen

    def __repr__(self):
        return '{}(rate_limit={!r}, tokens={!r}, last_update={!r})'.format(
            self.__class__.__name__,
            self.rate_limit,
            self.tokens,
            self.last_update,
        )

    __str__ = __repr__
