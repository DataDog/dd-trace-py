import math

import bm


class RateLimiter(bm.Scenario):
    rate_limit: int
    time_window: int
    num_windows: int

    def run(self):
        from time import time_ns

        from ddtrace.internal.rate_limiter import RateLimiter

        rate_limiter = RateLimiter(rate_limit=self.rate_limit, time_window=self.time_window)

        def _(loops):
            # Divide the operations into self.num_windows time windows
            # DEV: We want to exercise the rate limiter across multiple windows, and we
            # want to ensure we get consistency in the number of windows we are using
            start = time_ns()
            windows = [start + (i * self.time_window) for i in range(self.num_windows)]
            per_window = math.floor(loops / self.num_windows)

            for _ in windows:
                for _ in range(per_window):
                    rate_limiter.is_allowed()

        yield _
