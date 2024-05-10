class RateLimiter:
    """
    A token bucket rate limiter implementation
    """
    def __init__(self, rate_limit: int, time_window: float = 1e9):
        """
        Constructor for RateLimiter

        :param rate_limit: The rate limit to apply for number of requests per second.
            rate limit > 0 max number of requests to allow per second,
            rate limit == 0 to disallow all requests,
            rate limit < 0 to allow all requests
        :type rate_limit: :obj:`int`
        :param time_window: The time window where the rate limit applies in nanoseconds. default value is 1 second.
        :type time_window: :obj:`float`
        """

    def is_allowed(self, timestamp_ns: int) -> bool:
        """
        Check whether the current request is allowed or not

        This method will also reduce the number of available tokens by 1

        :param int timestamp_ns: timestamp in nanoseconds for the current request.
        :returns: Whether the current request is allowed or not
        :rtype: :obj:`bool`
        """

    @property
    def rate_limit(self) -> int:
        """
        Return the rate limit value

        :returns: The rate limit value
        :rtype: :obj:`int`
        """

    @property
    def effective_rate(self) -> float:
        """
        Return the effective sample rate of this rate limiter

        :returns: Effective sample rate value 0.0 <= rate <= 1.0
        :rtype: :obj:`float``
        """
