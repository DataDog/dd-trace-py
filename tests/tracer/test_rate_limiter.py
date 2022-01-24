from __future__ import division

import pytest

from ddtrace.internal import compat
from ddtrace.internal.rate_limiter import RateLimiter


def nanoseconds(x):
    # Helper to iterate over x seconds in nanosecond steps
    return range(0, int(1e9 * x), int(1e9))


def test_rate_limiter_init():
    limiter = RateLimiter(rate_limit=100)
    assert limiter.rate_limit == 100
    assert limiter.tokens == 100
    assert limiter.max_tokens == 100
    assert limiter.last_update_ns <= compat.monotonic_ns()


def test_rate_limiter_rate_limit_0():
    limiter = RateLimiter(rate_limit=0)
    assert limiter.rate_limit == 0
    assert limiter.tokens == 0
    assert limiter.max_tokens == 0

    now_ns = compat.monotonic_ns()
    for i in nanoseconds(10000):
        # Make sure the time is different for every check
        assert limiter.is_allowed(now_ns + i) is False


def test_rate_limiter_rate_limit_negative():
    limiter = RateLimiter(rate_limit=-1)
    assert limiter.rate_limit == -1
    assert limiter.tokens == -1
    assert limiter.max_tokens == -1

    now_ns = compat.monotonic_ns()
    for i in nanoseconds(10000):
        # Make sure the time is different for every check
        assert limiter.is_allowed(now_ns + i) is True


@pytest.mark.parametrize("rate_limit", [1, 10, 50, 100, 500, 1000])
def test_rate_limiter_is_allowed(rate_limit):
    limiter = RateLimiter(rate_limit=rate_limit)

    def check_limit(time_ns):
        # Up to the allowed limit is allowed
        for _ in range(rate_limit):
            assert limiter.is_allowed(time_ns) is True

        # Any over the limit is disallowed
        for _ in range(1000):
            assert limiter.is_allowed(time_ns) is False

    # Start time
    now = compat.monotonic_ns()

    # Check the limit for 5 time frames
    for i in nanoseconds(5):
        # Keep the same timeframe
        check_limit(now + i)


def test_rate_limiter_is_allowed_large_gap():
    limiter = RateLimiter(rate_limit=100)

    # Start time
    now_ns = compat.monotonic_ns()
    # Keep the same timeframe
    for _ in range(100):
        assert limiter.is_allowed(now_ns) is True

    # Large gap before next call to `is_allowed()`
    for _ in range(100):
        assert limiter.is_allowed(now_ns + (1e9 * 100)) is True


def test_rate_limiter_is_allowed_small_gaps():
    limiter = RateLimiter(rate_limit=100)

    # Start time
    now_ns = compat.monotonic_ns()
    gap = 1e9 / 100
    # Keep incrementing by a gap to keep us at our rate limit
    for i in nanoseconds(10000):
        # Keep the same timeframe
        time_ns = now_ns + (gap * i)

        assert limiter.is_allowed(time_ns) is True


def test_rate_liimter_effective_rate_rates():
    limiter = RateLimiter(rate_limit=100)

    # Static rate limit window
    starting_window_ns = compat.monotonic_ns()
    for _ in range(100):
        assert limiter.is_allowed(starting_window_ns) is True
        assert limiter.effective_rate == 1.0
        assert limiter.current_window_ns == starting_window_ns

    for i in range(1, 101):
        assert limiter.is_allowed(starting_window_ns) is False
        rate = 100 / (100 + i)
        assert limiter.effective_rate == rate
        assert limiter.current_window_ns == starting_window_ns

    prev_rate = 0.5
    window_ns = starting_window_ns + 1e9

    for i in range(100):
        assert limiter.is_allowed(window_ns) is True
        assert limiter.effective_rate == 0.75
        assert limiter.current_window_ns == window_ns

    for i in range(1, 101):
        assert limiter.is_allowed(window_ns) is False
        rate = 100 / (100 + i)
        assert limiter.effective_rate == (rate + prev_rate) / 2
        assert limiter.current_window_ns == window_ns


def test_rate_limiter_effective_rate_starting_rate():
    limiter = RateLimiter(rate_limit=1)

    now_ns = compat.monotonic_ns()

    # Default values
    assert limiter.current_window_ns == 0
    assert limiter.prev_window_rate is None

    # Accessing the effective rate doesn't change anything
    assert limiter.effective_rate == 1.0
    assert limiter.current_window_ns == 0
    assert limiter.prev_window_rate is None

    # Calling `.is_allowed()` updates the values
    assert limiter.is_allowed(now_ns) is True
    assert limiter.effective_rate == 1.0
    assert limiter.current_window_ns == now_ns
    assert limiter.prev_window_rate is None

    # Gap of 0.9999 seconds, same window
    time_ns = now_ns + (0.9999 * 1e9)
    assert limiter.is_allowed(time_ns) is False
    # DEV: We have rate_limit=1 set
    assert limiter.effective_rate == 0.5
    assert limiter.current_window_ns == now_ns
    assert limiter.prev_window_rate is None

    # Gap of 1.0 seconds, new window
    time_ns = now_ns + 1e9
    assert limiter.is_allowed(time_ns) is True
    assert limiter.effective_rate == 0.75
    assert limiter.current_window_ns == (now_ns + 1e9)
    assert limiter.prev_window_rate == 0.5

    # Gap of 1.9999 seconds, same window
    time_ns = now_ns + (1.9999 * 1e9)
    assert limiter.is_allowed(time_ns) is False
    assert limiter.effective_rate == 0.5
    assert limiter.current_window_ns == (now_ns + 1e9)  # Same as old window
    assert limiter.prev_window_rate == 0.5

    # Large gap of 100 seconds, new window
    time_ns = now_ns + (100.0 * 1e9)
    assert limiter.is_allowed(time_ns) is True
    assert limiter.effective_rate == 0.75
    assert limiter.current_window_ns == (now_ns + (100.0 * 1e9))
    assert limiter.prev_window_rate == 0.5
