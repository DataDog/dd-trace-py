"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import logging

from json import loads
from threading import Lock

log = logging.getLogger(__name__)

MAX_TRACE_ID = 2 ** 64

# Has to be the same factor and key as the Agent to allow chained sampling
KNUTH_FACTOR = 1111111111111111111
SAMPLE_RATE_METRIC_KEY = "_sample_rate"

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
        sampled = ((span.trace_id * KNUTH_FACTOR) % MAX_TRACE_ID) <= self.sampling_id_threshold

        return sampled

class RateByServiceSampler(object):
    """Sampler based on a rate, by service

    Keep (100 * `sample_rate`)% of the traces.
    The sample rate is kept independently for each service/env tuple.
    """

    def __init__(self, sample_rate=1):
        self._lock = Lock()
        self._by_service_samplers = {}
        self._default_key = self._key(None, None)
        self._by_service_samplers[self._default_key] = RateSampler(sample_rate)

    def _key(self, service="", env=""):
        service = service or ""
        env = env or ""
        return "service:" + service + ",env:" + env

    def _set_sample_rate_by_key(self, sample_rate, key):
        with self._lock:
            if key in self._by_service_samplers:
                self._by_service_samplers[key].set_sample_rate(sample_rate)
            else:
                self._by_service_samplers[key] = RateSampler(sample_rate)

    def set_sample_rate(self, sample_rate, service="", env=""):
        self._set_sample_rate_by_key(sample_rate, self._key(service, env))

    def sample(self, span):
        tags = span.tracer().tags
        env = tags['env'] if 'env' in tags else None
        key = self._key(span.service, env)
        with self._lock:
            if key in self._by_service_samplers:
                return self._by_service_samplers[key].sample(span)
            return self._by_service_samplers[self._default_key].sample(span)

    def set_sample_rates_from_json(self, body):
        try:
            log.debug("setting sample rates from JSON '%s'" % repr(body))
            if not isinstance(body, str):
                body = body.decode('utf-8')
            content = loads(body)
        except ValueError as err:
            log.error("unable to load JSON '%s': %s" % (body, err))
            return

        rate_by_service = content['rate_by_service']
        for key, sample_rate in rate_by_service.iteritems():
            self._set_sample_rate_by_key(sample_rate, key)
        with self._lock:
            for key in self._by_service_samplers:
                if key not in rate_by_service and key != self._default_key:
                    del self.by_service_samplers[key]
