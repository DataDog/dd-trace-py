from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import random
import time
from typing import TYPE_CHECKING
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.internal.threads import Lock


if TYPE_CHECKING:
    from _thread import LockType


class RateLimiter(object):
    """
    A token bucket rate limiter implementation
    """

    __slots__ = (
        "_lock",
        "current_window_ns",
        "time_window",
        "last_update_ns",
        "max_tokens",
        "prev_window_rate",
        "rate_limit",
        "tokens",
        "tokens_allowed",
        "tokens_total",
    )

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
        self.rate_limit = rate_limit
        self.time_window = time_window
        self.tokens = rate_limit  # type: float
        self.max_tokens = rate_limit

        self.last_update_ns = time.monotonic_ns()

        self.current_window_ns = 0  # type: float
        self.tokens_allowed = 0
        self.tokens_total = 0
        self.prev_window_rate = None  # type: Optional[float]

        self._lock = Lock()

    def is_allowed(self) -> bool:
        """
        Check whether the current request is allowed or not

        This method will also reduce the number of available tokens by 1

        :returns: Whether the current request is allowed or not
        :rtype: :obj:`bool`
        """
        # rate limits are tested and mocked in pytest so we need to compute the timestamp here
        # (or move the unit tests to rust)
        timestamp_ns = time.monotonic_ns()
        allowed = self._is_allowed(timestamp_ns)
        # Update counts used to determine effective rate
        self._update_rate_counts(allowed, timestamp_ns)
        return allowed

    def _update_rate_counts(self, allowed: bool, timestamp_ns: int) -> None:
        # No tokens have been seen yet, start a new window
        if not self.current_window_ns:
            self.current_window_ns = timestamp_ns

        # If more time than the configured time window
        # has past since last window, reset
        # DEV: We are comparing nanoseconds, so 1e9 is 1 second
        elif timestamp_ns - self.current_window_ns >= self.time_window:
            # Store previous window's rate to average with current for `.effective_rate`
            self.prev_window_rate = self._current_window_rate()
            self.tokens_allowed = 0
            self.tokens_total = 0
            self.current_window_ns = timestamp_ns

        # Keep track of total tokens seen vs allowed
        if allowed:
            self.tokens_allowed += 1
        self.tokens_total += 1

    def _is_allowed(self, timestamp_ns: int) -> bool:
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

    def _replenish(self, timestamp_ns: int) -> None:
        try:
            # If we are at the max, we do not need to add any more
            if self.tokens == self.max_tokens:
                return

            # Add more available tokens based on how much time has passed
            # DEV: We store as nanoseconds, convert to seconds
            elapsed = (timestamp_ns - self.last_update_ns) / self.time_window
        finally:
            # always update the timestamp
            # we can't update at the beginning of the function, since if we did, our calculation for
            # elapsed would be incorrect
            self.last_update_ns = timestamp_ns

        # Update the number of available tokens, but ensure we do not exceed the max
        self.tokens = min(
            self.max_tokens,
            self.tokens + (elapsed * self.rate_limit),
        )

    def _current_window_rate(self) -> float:
        # No tokens have been seen, effectively 100% sample rate
        # DEV: This is to avoid division by zero error
        if not self.tokens_total:
            return 1.0

        # Get rate of tokens allowed
        return self.tokens_allowed / self.tokens_total

    @property
    def effective_rate(self) -> float:
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


class RateLimitExceeded(Exception):
    pass


@dataclass
class BudgetRateLimiterWithJitter:
    """A budget rate limiter with jitter.

    The jitter is induced by a uniform distribution. The rate limit can be
    specified with ``limit_rate`` and the time scale can be controlled with the
    ``tau`` parameter (which defaults to 1 second). The initial budget is the
    product between ``limit_rate`` and the time-scale parameter ``tau``, which
    is also taken as the maximum budget. By default, the ``RateLimitExceeded``
    exception is raised when the rate limit is exceeded. This can be changed by
    setting ``raise_on_exceed`` to ``False``. The ``on_exceed`` argument can be
    used to pass a callback that is to be called whenever the rate limit is
    exceeded. The ``call_once`` argument controls whether the callback should be
    called only once for every rate limit excess or every time the rate limiter
    is invoked.

    Instances of this class can also be used as decorators.

    Since the initial and maximum budget are set to ``limit_rate * tau``, the
    rate limiter could have an initial burst phase. When this is not desired,
    ``tau`` should be set to ``1 / limit_rate`` to ensure an initial and maximum
    budget of ``1``.
    """

    limit_rate: float
    tau: float = 1.0
    raise_on_exceed: bool = True
    on_exceed: Optional[Callable] = None
    call_once: bool = False
    budget: float = field(init=False)
    max_budget: float = field(init=False)
    last_time: float = field(init=False, default_factory=time.monotonic)
    _lock: LockType = field(init=False, default_factory=Lock)
    _pending: int = field(init=False, default=0)

    def __post_init__(self):
        if self.limit_rate == float("inf"):
            self.budget = self.max_budget = float("inf")
        elif self.limit_rate:
            self.budget = self.max_budget = self.limit_rate * self.tau
        else:
            self.budget = self.max_budget = 1.0
        self._on_exceed_called = False

    def _accrue(self) -> None:
        """Add the budget that has become available since the last look.

        The caller is expected to hold the lock.
        """
        now = time.monotonic()
        self.budget += self.limit_rate * (now - self.last_time) * (0.5 + random.random())  # jitter
        self.last_time = now

    def _cap(self) -> None:
        """Discard any budget beyond the maximum."""
        if self.budget > self.max_budget:
            self.budget = self.max_budget

    def has_budget(self) -> bool:
        """Whether a call would be allowed, without consuming any budget.

        For a caller that may take this decision several times, or make several
        separate later commits, from one look -- a shared unit-of-execution
        budget peeked once and spent by many independent probes as they each
        emit. Leaves ``last_time`` untouched, so a peek does not shrink the
        window a later :meth:`consume` or :meth:`limit` accrues over: whatever
        elapsed since the last real accrual still counts, whether that was
        another peek or a commit.
        """
        with self._lock:
            elapsed = time.monotonic() - self.last_time
            projected = self.budget + self.limit_rate * elapsed * (0.5 + random.random())
            return min(self.max_budget, projected) >= 1.0

    def reserve(self) -> bool:
        """Accrue up to now and check, advancing the accrual clock.

        For a caller that takes exactly one decision per unit of time and,
        separately, spends it later via :meth:`spend` only if the decision
        turned out to matter. Advancing the clock here -- unlike
        :meth:`has_budget` -- is what keeps the accrual window anchored to the
        cadence of those decisions (one every invocation) rather than to the
        cadence of the spends that follow them (one every emission, which can
        lag its decision by however long the invocation takes).

        A reservation that returns ``True`` is allowed to go unspent -- the
        caller may still decide, for unrelated reasons, not to follow through
        -- but every :meth:`spend` must be matched by one. Returning ``False``
        reserves nothing, since there is nothing for a later spend to draw on.
        """
        with self._lock:
            self._accrue()
            self._cap()
            if self.budget < 1.0:
                return False
            self._pending += 1
            return True

    def spend(self, amount: float = 1.0) -> None:
        """Debit budget already reserved via :meth:`reserve`, without accruing more.

        The accrual for this decision already happened in :meth:`reserve`; the
        time between that reservation and this spend belongs to whatever the
        caller was doing in between, not to this limiter.

        :raises RuntimeError: If there is no outstanding reservation to spend --
            calling this without a preceding successful :meth:`reserve` is a
            caller bug, not a rate-limiting outcome, so it is not silently
            tolerated.
        """
        with self._lock:
            if self._pending <= 0:
                raise RuntimeError(f"{self!r}.spend() called without a matching reserve()")
            self._pending -= 1
            self.budget -= amount

    def consume(self, amount: float = 1.0) -> None:
        """Spend budget whether or not there is any left.

        For callers that have already incurred the cost by the time they get
        here, so refusing is not an option. The budget is allowed to go into
        deficit, and the overspend is repaid before anything is let through
        again.
        """
        with self._lock:
            self._accrue()
            self._cap()
            self.budget -= amount

    def limit(self, f: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any) -> Any:
        """Make rate-limited calls to a function with the given arguments."""
        should_call = False
        with self._lock:
            self._accrue()
            should_call = self.budget >= 1.0
            self._cap()
            if should_call:
                self.budget -= 1.0

        if should_call:
            self._on_exceed_called = False
            return f(*args, **kwargs) if f is not None else None

        if self.on_exceed is not None:
            if not self.call_once:
                self.on_exceed()
            elif not self._on_exceed_called:
                self.on_exceed()
                self._on_exceed_called = True

        if self.raise_on_exceed:
            raise RateLimitExceeded()
        else:
            return RateLimitExceeded

    def __call__(self, f: Callable[..., Any]) -> Callable[..., Any]:
        def limited_f(*args, **kwargs):
            return self.limit(f, *args, **kwargs)

        return limited_f
