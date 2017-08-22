"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import logging

from random import getrandbits

log = logging.getLogger(__name__)

MAX_TRACE_ID = 2 ** 64

# Has to be the same factor and key as the Agent to allow chained sampling
KNUTH_FACTOR = 1111111111111111111
SAMPLE_RATE_METRIC_KEY = "_sample_rate"

class AllSampler(object):
    """Sampler sampling all the traces"""

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

        self.set_sample_rate(sample_rate)

        log.info("initialized RateSampler, sample %s%% of traces", 100 * sample_rate)

    def set_sample_rate(self, sample_rate):
        self.sample_rate = sample_rate
        self.sampling_id_threshold = sample_rate * MAX_TRACE_ID

    def sample(self, span):
        try:
            processed_id = ((span.trace_id * KNUTH_FACTOR) % MAX_TRACE_ID)
        except AttributeError:
            # When there's no trace_id, pick up a totally random one,
            # this is typically used by the distributed sampler, it does
            # not care about applying the same decision on a given span
            # as the decision is taken only once, by design.
            processed_id = getrandbits(64)
        span.set_sampled(processed_id <= self.sampling_id_threshold)
        try:
            if callable(getattr(span, 'set_metric')):
                span.set_metric(SAMPLE_RATE_METRIC_KEY, self.sample_rate)
        except AttributeError:
            pass

class DistributedSampled(object):
    """ Holds the sampled attribute for distributed traces

        Distributed sampling and sampling are two different things.
        In classic, local sampling, one decides to send or not the
        trace to the agent depending on the sampled attribute.

        In distributed tracing, the root span sets the sampled value
        to true or false, and this is propagated to all child spans.
        Then the trace is sent to the agent, *and* it should be send
        to the API also.
    """

    def __init__(self, span):
        """ Creates a basic sampling proxy with refers to a span. """
        self.span = span

    def set_sampled(self, sampled):
        """ Marks the span as sampled. """
        if self.span:
            self.span.set_sampling_priority(1 if sampled else 0)
