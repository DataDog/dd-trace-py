import bm


class RateLimiter(bm.Scenario):
    rate_limit = bm.var(type=int)
    time_window = bm.var(type=float)

    def run(self):
        from ddtrace.internal.compat import time_ns
        from ddtrace.internal.rate_limiter import RateLimiter

        rate_limiter = RateLimiter(rate_limit=self.rate_limit, time_window=self.time_window)

        def _(loops):
            for _ in range(loops):
                rate_limiter.is_allowed(time_ns())

        yield _
