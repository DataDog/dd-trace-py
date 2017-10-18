from __future__ import division

import unittest
import random

from ddtrace.tracer import Tracer
from ddtrace.span import Span
from ddtrace.sampler import RateSampler, AllSampler, RateByServiceSampler, SAMPLE_RATE_METRIC_KEY, _key, _default_key
from ddtrace.compat import iteritems
from .test_tracer import DummyWriter
from .util import patch_time


class RateSamplerTest(unittest.TestCase):

    def test_sample_rate_deviation(self):
        writer = DummyWriter()

        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = Tracer()
            tracer.writer = writer

            tracer.sampler = RateSampler(sample_rate)

            random.seed(1234)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) == sample_rate

            # Less than 2% deviation when "enough" iterations (arbitrary, just check if it converges)
            deviation = abs(len(samples) - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.02, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

    def test_deterministic_behavior(self):
        """ Test that for a given trace ID, the result is always the same """
        writer = DummyWriter()

        tracer = Tracer()
        tracer.writer = writer

        tracer.sampler = RateSampler(0.5)

        random.seed(1234)

        for i in range(10):
            span = tracer.trace(i)
            span.finish()

            samples = writer.pop()
            assert len(samples) <= 1, "there should be 0 or 1 spans"
            sampled = (1 == len(samples))
            for j in range(10):
                other_span = Span(tracer, i, trace_id=span.trace_id)
                assert sampled == tracer.sampler.sample(other_span), "sampling should give the same result for a given trace_id"

class RateByServiceSamplerTest(unittest.TestCase):
    def test_default_key(self):
        assert "service:,env:" == _default_key, "default key should correspond to no service and no env"

    def test_key(self):
        assert _default_key == _key()
        assert "service:mcnulty,env:" == _key(service="mcnulty")
        assert "service:,env:test" == _key(env="test")
        assert "service:mcnulty,env:test" == _key(service="mcnulty", env="test")
        assert "service:mcnulty,env:test" == _key("mcnulty", "test")

    def test_sample_rate_deviation(self):
        writer = DummyWriter()

        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = Tracer()
            tracer.configure(sampler=AllSampler(), priority_sampling=True)
            tracer.priority_sampler.set_sample_rate(sample_rate)
            tracer.writer = writer

            random.seed(1234)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()
            samples_with_high_priority = 0
            for sample in samples:
                if sample._sampling_priority:
                    if sample._sampling_priority > 0:
                        samples_with_high_priority += 1
                else:
                    assert 0 == sample._sampling_priority, "when priority sampling is on, priority should be 0 when trace is to be dropped"

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) is None

            # Less than 2% deviation when "enough" iterations (arbitrary, just check if it converges)
            deviation = abs(samples_with_high_priority - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.02, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

    def test_set_sample_rates_from_json(self):
        cases = {
            '{"rate_by_service": {"service:,env:": 1}}': {"service:,env:":1},
            '{"rate_by_service": {"service:,env:": 1, "service:mcnulty,env:dev": 0.33, "service:postgres,env:dev": 0.7}}': {"service:,env:":1, "service:mcnulty,env:dev":0.33, "service:postgres,env:dev":0.7},
            '{"rate_by_service": {"service:,env:": 1, "service:mcnulty,env:dev": 0.25, "service:postgres,env:dev": 0.5, "service:redis,env:prod": 0.75}}': {"service:,env:":1, "service:mcnulty,env:dev": 0.25, "service:postgres,env:dev": 0.5, "service:redis,env:prod": 0.75}
        }

        writer = DummyWriter()

        tracer = Tracer()
        tracer.configure(sampler=AllSampler(), priority_sampling=True)
        priority_sampler = tracer.priority_sampler
        tracer.writer = writer
        keys = list(cases)
        for k in keys:
            case = cases[k]
            priority_sampler.set_sample_rates_from_json(k)
            rates = {}
            for k,v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates
        # It's important to also test in reverse mode for we want to make sure key deletion
        # works as well as key insertion (and doing this both ways ensures we trigger both cases)
        keys.reverse()
        for k in keys:
            case = cases[k]
            priority_sampler.set_sample_rates_from_json(k)
            rates = {}
            for k,v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates
