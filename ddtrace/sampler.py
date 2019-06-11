"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
from .compat import iteritems
from .internal.logger import get_logger

log = get_logger(__name__)

MAX_TRACE_ID = 2 ** 64

# Has to be the same factor and key as the Agent to allow chained sampling
KNUTH_FACTOR = 1111111111111111111


class AllSampler(object):
    """Sampler sampling all the traces"""

    def sample(self, span):
        return True


class RateSampler(object):
    """Sampler based on a rate

    Keep (100 * `sample_rate`)% of the traces.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate=1):
        if sample_rate <= 0:
            log.error('sample_rate is negative or null, disable the Sampler')
            sample_rate = 1
        elif sample_rate > 1:
            sample_rate = 1

        self.set_sample_rate(sample_rate)

        log.debug('initialized RateSampler, sample %s%% of traces', 100 * sample_rate)

    def set_sample_rate(self, sample_rate):
        self.sample_rate = sample_rate
        self.sampling_id_threshold = sample_rate * MAX_TRACE_ID

    def sample(self, span):
        sampled = ((span.trace_id * KNUTH_FACTOR) % MAX_TRACE_ID) <= self.sampling_id_threshold

        return sampled


class RateByServiceSampler(object):
    """Sampler based on a rate, by service

    Keep (100 * `sample_rate`)% of the traces.
    The sample rate is kept independently for each service/env tuple.
    """

    @staticmethod
    def _key(service=None, env=None):
        """Compute a key with the same format used by the Datadog agent API."""
        service = service or ''
        env = env or ''
        return 'service:' + service + ',env:' + env

    def __init__(self, sample_rate=1):
        self.sample_rate = sample_rate
        self._by_service_samplers = self._get_new_by_service_sampler()

    def _get_new_by_service_sampler(self):
        return {
            self._default_key: RateSampler(self.sample_rate)
        }

    def set_sample_rate(self, sample_rate, service='', env=''):
        self._by_service_samplers[self._key(service, env)] = RateSampler(sample_rate)

    def sample(self, span):
        tags = span.tracer().tags
        env = tags['env'] if 'env' in tags else None
        key = self._key(span.service, env)
        return self._by_service_samplers.get(
            key, self._by_service_samplers[self._default_key]
        ).sample(span)

    def set_sample_rate_by_service(self, rate_by_service):
        new_by_service_samplers = self._get_new_by_service_sampler()
        for key, sample_rate in iteritems(rate_by_service):
            new_by_service_samplers[key] = RateSampler(sample_rate)

        self._by_service_samplers = new_by_service_samplers


# Default key for service with no specific rate
RateByServiceSampler._default_key = RateByServiceSampler._key()
