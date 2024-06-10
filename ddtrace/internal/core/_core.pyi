import typing

class RateLimiter:
    """
    A token bucket rate limiter implementation
    """

    rate_limit: int
    time_window: float
    effective_rate: float
    current_window_rate: float
    prev_window_rate: typing.Optional[float]
    tokens: float
    max_tokens: float
    tokens_allowed: int
    tokens_total: int
    last_update_ns: float
    current_window_ns: float

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
    def is_allowed(self, timestamp_ns: typing.Optional[int] = None) -> bool:
        """
        Check whether the current request is allowed or not

        This method will also reduce the number of available tokens by 1

        :param int timestamp_ns: timestamp in nanoseconds for the current request. [deprecated]
        :returns: Whether the current request is allowed or not
        :rtype: :obj:`bool`
        """
    def _is_allowed(self, timestamp_ns: int) -> bool:
        """
        Internal method to check whether the current request is allowed or not

        :param int timestamp_ns: timestamp in nanoseconds for the current request.
        :returns: Whether the current request is allowed or not
        :rtype: :obj:`bool`
        """
