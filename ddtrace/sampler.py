import logging

from .span import MAX_TRACE_ID

log = logging.getLogger(__name__)


class RateSampler(object):
    """RateSampler manages the client-side trace sampling based on a rate

    Keep (100 * sample_rate)% of the traces.
    Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
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

    def sample(self, span):
        span.sampled = span.trace_id <= self.sampling_id_threshold
        # `weight` is an attribute applied to all spans to help scaling related statistics
        span.weight = 1 / (self.sample_rate or 1)
