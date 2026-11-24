import time
from unittest import mock

import pytest

from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.internal.rate_limiter import RateLimitExceeded


def nanoseconds(x, time_window):
    # Helper to iterate over x time windows in nanosecond steps
    return range(0, int(time_window * x), int(time_window))


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_init(time_window):
    limiter = RateLimiter(rate_limit=100, time_window=time_window)
    assert limiter.rate_limit == 100
    assert limiter.tokens == 100
    assert limiter.max_tokens == 100
    assert limiter.last_update_ns <= time.monotonic_ns()


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_rate_limit_0(time_window):
    limiter = RateLimiter(rate_limit=0, time_window=time_window)
    assert limiter.rate_limit == 0
    assert limiter.tokens == 0
    assert limiter.max_tokens == 0

    now_ns = time.monotonic_ns()
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns") as clock:
        for i in nanoseconds(10000, time_window):
            clock.return_value = now_ns + i
            assert limiter.is_allowed() is False


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_rate_limit_negative(time_window):
    limiter = RateLimiter(rate_limit=-1, time_window=time_window)
    assert limiter.rate_limit == -1
    assert limiter.tokens == -1
    assert limiter.max_tokens == -1

    now_ns = time.monotonic_ns()
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns") as clock:
        for i in nanoseconds(10000, time_window):
            clock.return_value = now_ns + i
            assert limiter.is_allowed() is True


@pytest.mark.parametrize("rate_limit", [1, 10, 50, 100, 500, 1000])
@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_is_allowed(rate_limit, time_window):
    limiter = RateLimiter(rate_limit=rate_limit, time_window=time_window)

    def check_limit():
        # Up to the allowed limit is allowed
        for _ in range(rate_limit):
            assert limiter.is_allowed() is True

        # Any over the limit is disallowed
        for _ in range(1000):
            assert limiter.is_allowed() is False

    # Start time
    now = time.monotonic_ns()

    # Check the limit for 5 time frames
    for i in nanoseconds(5, time_window):
        # Keep the same timeframe
        with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=now + i):
            check_limit()


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_is_allowed_large_gap(time_window):
    limiter = RateLimiter(rate_limit=100, time_window=time_window)

    # Start time
    now_ns = time.monotonic_ns()
    # Keep the same timeframe
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=now_ns):
        for _ in range(100):
            assert limiter.is_allowed() is True

    # Large gap before next call to `is_allowed()`
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=now_ns + (time_window * 100)):
        for _ in range(100):
            assert limiter.is_allowed() is True


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_is_allowed_small_gaps(time_window):
    limiter = RateLimiter(rate_limit=100, time_window=time_window)

    # Start time
    now_ns = time.monotonic_ns()
    gap = 1e9 / 100
    # Keep incrementing by a gap to keep us at our rate limit
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns") as clock:
        for i in nanoseconds(10000, time_window):
            clock.return_value = now_ns + (gap * i)
            assert limiter.is_allowed() is True


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_liimter_effective_rate_rates(time_window):
    limiter = RateLimiter(rate_limit=100, time_window=time_window)

    # Static rate limit window
    starting_window_ns = time.monotonic_ns()
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=starting_window_ns):
        for _ in range(100):
            assert limiter.is_allowed() is True
            assert limiter.effective_rate == 1.0
            assert limiter.current_window_ns == starting_window_ns

        for i in range(1, 101):
            assert limiter.is_allowed() is False
            rate = 100 / (100 + i)
            assert limiter.effective_rate == rate
            assert limiter.current_window_ns == starting_window_ns

    prev_rate = 0.5
    window_ns = starting_window_ns + time_window
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=window_ns):
        for _ in range(100):
            assert limiter.is_allowed() is True
            assert limiter.effective_rate == 0.75
            assert limiter.current_window_ns == window_ns

        for i in range(1, 101):
            assert limiter.is_allowed() is False
            rate = 100 / (100 + i)
            assert limiter.effective_rate == (rate + prev_rate) / 2
            assert limiter.current_window_ns == window_ns


@pytest.mark.parametrize("time_window", [1e3, 1e6, 1e9])
def test_rate_limiter_effective_rate_starting_rate(time_window):
    limiter = RateLimiter(rate_limit=1, time_window=time_window)

    now_ns = time.monotonic_ns()

    # Default values
    assert limiter.current_window_ns == 0
    assert limiter.prev_window_rate is None

    # Accessing the effective rate doesn't change anything
    assert limiter.effective_rate == 1.0
    assert limiter.current_window_ns == 0
    assert limiter.prev_window_rate is None

    # Calling `.is_allowed()` updates the values
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=now_ns):
        assert limiter.is_allowed() is True
        assert limiter.effective_rate == 1.0
        assert limiter.current_window_ns == now_ns
        assert limiter.prev_window_rate is None

    # Gap of 0.85 seconds, same window
    time_ns = now_ns + (0.85 * time_window)
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=time_ns):
        assert limiter.is_allowed() is False
        # DEV: We have rate_limit=1 set
        assert limiter.effective_rate == 0.5
        assert limiter.current_window_ns == now_ns
        assert limiter.prev_window_rate is None

    # Gap of 1.0 seconds, new window
    time_ns = now_ns + time_window
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=time_ns):
        assert limiter.is_allowed() is True
        assert limiter.effective_rate == 0.75
        assert limiter.current_window_ns == (now_ns + time_window)
        assert limiter.prev_window_rate == 0.5

    # Gap of 1.85 seconds, same window
    time_ns = now_ns + (1.85 * time_window)
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=time_ns):
        assert limiter.is_allowed() is False
        assert limiter.effective_rate == 0.5
        assert limiter.current_window_ns == (now_ns + time_window)  # Same as old window
        assert limiter.prev_window_rate == 0.5

    # Large gap of 100 seconds, new window
    time_ns = now_ns + (100.0 * time_window)
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=time_ns):
        assert limiter.is_allowed() is True
        assert limiter.effective_rate == 0.75
        assert limiter.current_window_ns == (now_ns + (100.0 * time_window))
        assert limiter.prev_window_rate == 0.5


def test_rate_limiter_3():
    limiter = RateLimiter(rate_limit=2)

    for i in range(3):
        decision = limiter.is_allowed()
        # the first two should be allowed, the third should not
        if i < 2:
            assert decision is True
        else:
            assert decision is False


@pytest.mark.parametrize("rate_limit", list(range(10)))
def test_rate_limiter_with_jitter_expected_calls(rate_limit):
    limiter = BudgetRateLimiterWithJitter(limit_rate=rate_limit)
    acc = []

    exceeded = 0
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic", return_value=limiter.last_time):
        for i in range(rate_limit * 10):
            try:
                limiter.limit(lambda n: acc.append(n), i)
            except RateLimitExceeded:
                exceeded += 1

    assert not set(range(rate_limit)) < set(acc)
    assert len(acc) == rate_limit
    assert exceeded == rate_limit * 9


@pytest.mark.parametrize("rate_limit", list(range(1, 10)))
def test_rate_limiter_with_jitter_expected_calls_tau(rate_limit):
    limiter = BudgetRateLimiterWithJitter(limit_rate=rate_limit, tau=1.0 / rate_limit)
    acc = []

    exceeded = 0
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic", return_value=limiter.last_time):
        for i in range(rate_limit * 10):
            try:
                limiter.limit(lambda n: acc.append(n), i)
            except RateLimitExceeded:
                exceeded += 1

    assert acc == [0]


@pytest.mark.parametrize("rate_limit", list(range(10)))
def test_rate_limiter_with_jitter_expected_calls_decorator(rate_limit):
    acc = []
    limiter = BudgetRateLimiterWithJitter(limit_rate=rate_limit)

    @limiter
    def appender(n):
        acc.append(n)

    exceeded = 0
    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic", return_value=limiter.last_time):
        for i in range(rate_limit * 10):
            try:
                appender(i)
            except RateLimitExceeded:
                exceeded += 1

    assert not set(range(rate_limit)) < set(acc)
    assert len(acc) == rate_limit
    assert exceeded == rate_limit * 9


def test_rate_limiter_with_jitter_called_once():
    callback = mock.Mock()
    limiter = BudgetRateLimiterWithJitter(limit_rate=1, on_exceed=callback, call_once=True)

    for _ in range(10):
        try:
            limiter.limit(lambda: None)
        except RateLimitExceeded:
            pass

    assert callback.call_count == 1


def test_rate_limiter_with_jitter_not_raise():
    limiter = BudgetRateLimiterWithJitter(limit_rate=1, raise_on_exceed=False)

    assert [limiter.limit(lambda: None) for _ in range(10)][1:] == [RateLimitExceeded] * 9


def test_rate_limiter_has_budget_does_not_consume():
    limiter = BudgetRateLimiterWithJitter(limit_rate=1, raise_on_exceed=False)

    assert limiter.has_budget() is True
    assert limiter.has_budget() is True
    assert limiter.limit() is not RateLimitExceeded
    assert limiter.has_budget() is False


def test_rate_limiter_consume_goes_into_deficit():
    limiter = BudgetRateLimiterWithJitter(limit_rate=1, raise_on_exceed=False)

    # Spent whether or not there is budget left, and the overspend has to be
    # repaid before the next call is allowed.
    limiter.consume()
    limiter.consume()

    assert limiter.has_budget() is False
    limiter.budget += 1.0
    assert limiter.has_budget() is False


@pytest.mark.parametrize(
    "limit_rate,tau",
    [
        # limit_rate * tau rounds to 0.9999999999999999
        (49.0, 1.0 / 49.0),
        # A rate below one per tau
        (0.5, 1.0),
    ],
)
def test_rate_limiter_has_budget_with_max_budget_below_one(limit_rate, tau):
    limiter = BudgetRateLimiterWithJitter(limit_rate=limit_rate, tau=tau, raise_on_exceed=False)
    assert limiter.max_budget < 1.0

    limiter.consume()
    assert limiter.has_budget() is False

    # Enough time for the budget to replenish, even with the minimum jitter.
    limiter.last_time -= 10.0 / limit_rate

    # The peek agrees with what limit() would do.
    assert limiter.has_budget() is True
    assert limiter.limit() is not RateLimitExceeded


def test_rate_limiter_infinite_rate_with_no_elapsed_time():
    # inf * 0 is NaN, so with a coarse clock an infinite rate used to turn the
    # budget into NaN and reject every call from then on.
    limiter = BudgetRateLimiterWithJitter(limit_rate=float("inf"), raise_on_exceed=False)

    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic", return_value=limiter.last_time):
        for _ in range(3):
            assert limiter.has_budget() is True
            assert limiter.limit() is not RateLimitExceeded
        limiter.consume()
        assert limiter.has_budget() is True
        assert limiter.limit() is not RateLimitExceeded


@pytest.mark.parametrize("jitter,expected", [(0.1, False), (0.95, True)])
def test_rate_limiter_has_budget_agrees_with_limit(jitter, expected):
    # Looking at the budget brings it up to date, so the jitter for the time
    # elapsed is drawn once. A limit() right after must agree with the peek,
    # rather than drawing a more generous jitter for the same interval.
    limiter = BudgetRateLimiterWithJitter(limit_rate=1.0, raise_on_exceed=False)
    limiter.budget = 0.0
    now = limiter.last_time = time.monotonic()

    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic", return_value=now + 0.7):
        with mock.patch("ddtrace.internal.rate_limiter.random.random", return_value=jitter):
            assert limiter.has_budget() is expected

        with mock.patch("ddtrace.internal.rate_limiter.random.random", return_value=0.999999):
            assert limiter.has_budget() is expected
            assert (limiter.limit() is not RateLimitExceeded) is expected
