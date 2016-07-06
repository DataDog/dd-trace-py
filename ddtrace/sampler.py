"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""

import logging
import array

from .span import MAX_TRACE_ID

log = logging.getLogger(__name__)


class DefaultSampler(object):
    """Default sampler, sampling all the traces"""

    def sample(self, span):
        span.sampled = True


class RateSampler(object):
    """Sampler based on a rate

    Keep (100 * `sample_rate`)% of the traces.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate):
        if sample_rate <= 0:
            log.error("sample_rate is negative or null, disable the Sampler")
            sample_rate = 1
        elif sample_rate > 1:
            sample_rate = 1

        self.sample_rate = sample_rate
        self.sampling_id_threshold = sample_rate * MAX_TRACE_ID

        log.info("initialized RateSampler, sample %s%% of traces", 100 * sample_rate)

    def sample(self, span):
        span.sampled = span.trace_id <= self.sampling_id_threshold
        # `weight` is an attribute applied to all spans to help scaling related statistics
        span.weight = 1 / (self.sample_rate or 1)


class ThroughputSampler(object):
    """Sampler based on a limit over the trace volume

    Stop tracing once reached more than `limit` traces over the last `over` seconds.
    Count each sampled trace based on the modulo of its time inside a circular buffer, with 1s count bucket.
    """

    def __init__(self, limit, over):
        self.limit = limit
        self.over = over

        self._counter = array.array('L', [0] * self.over)
        self.last_track_time = 0

        log.info("initialized ThroughputSampler, sample up to %s traces over %s seconds", limit, over)

    def sample(self, span):
        now = int(span.start)
        last_track_time = self.last_track_time
        if now > last_track_time:
            self.last_track_time = now
            self.expire_buckets(last_track_time, now)

        span.sampled = self.count_traces() < self.limit

        if span.sampled:
            self._counter[self.key_from_time(now)] += 1

        return span

    def key_from_time(self, t):
        return t % self.over

    def expire_buckets(self, start, end):
        period = min(self.over, (end - start))
        for i in range(period):
            self._counter[self.key_from_time(start + i + 1)] = 0

    def count_traces(self):
        return sum(self._counter)
