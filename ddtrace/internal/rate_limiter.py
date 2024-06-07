from __future__ import division

import random
import threading
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401

import attr

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ..internal import compat
from ..internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from .core import RateLimiter as _RateLimiter


class RateLimiter(_RateLimiter):
    @property
    def _has_been_configured(self):
        return self.rate_limit != DEFAULT_SAMPLING_RATE_LIMIT

    def is_allowed(self, timestamp_ns: Optional[int] = None) -> bool:
        if timestamp_ns is not None:
            deprecate(
                "The `timestamp_ns` parameter is deprecated and will be removed in a future version."
                "Ratelimiter will use the current time.",
                category=DDTraceDeprecationWarning,
            )
        # rate limits are tested and mocked in pytest so we need to compute the timestamp here
        # (or move the unit tests to rust)
        return self._is_allowed(compat.monotonic_ns())


class RateLimitExceeded(Exception):
    pass


@attr.s
class BudgetRateLimiterWithJitter(object):
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

    limit_rate = attr.ib(type=float)
    tau = attr.ib(type=float, default=1.0)
    raise_on_exceed = attr.ib(type=bool, default=True)
    on_exceed = attr.ib(type=Callable, default=None)
    call_once = attr.ib(type=bool, default=False)
    budget = attr.ib(type=float, init=False)
    max_budget = attr.ib(type=float, init=False)
    last_time = attr.ib(type=float, init=False, factory=compat.monotonic)
    _lock = attr.ib(type=threading.Lock, init=False, factory=threading.Lock)

    def __attrs_post_init__(self):
        if self.limit_rate == float("inf"):
            self.budget = self.max_budget = float("inf")
        elif self.limit_rate:
            self.budget = self.max_budget = self.limit_rate * self.tau
        else:
            self.budget = self.max_budget = 1.0
        self._on_exceed_called = False

    def limit(self, f: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any) -> Any:
        """Make rate-limited calls to a function with the given arguments."""
        should_call = False
        with self._lock:
            now = compat.monotonic()
            self.budget += self.limit_rate * (now - self.last_time) * (0.5 + random.random())  # jitter
            should_call = self.budget >= 1.0
            if self.budget > self.max_budget:
                self.budget = self.max_budget
            self.last_time = now

        if should_call:
            self._on_exceed_called = False
            self.budget -= 1.0
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
