from __future__ import division

import unittest

from ddtrace.span import Span
from ddtrace.sampler import RateSampler, AllSampler, RateByServiceSampler
from ddtrace.compat import iteritems
from tests.test_tracer import get_dummy_tracer
from ddtrace.constants import SAMPLING_PRIORITY_KEY, SAMPLE_RATE_METRIC_KEY


class RateSamplerTest(unittest.TestCase):

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = get_dummy_tracer()
            writer = tracer.writer

            tracer.sampler = RateSampler(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) == sample_rate

            # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
            deviation = abs(len(samples) - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05, 'Deviation too high %f with sample_rate %f' % (deviation, sample_rate)

    def test_deterministic_behavior(self):
        """ Test that for a given trace ID, the result is always the same """
        tracer = get_dummy_tracer()
        writer = tracer.writer

        tracer.sampler = RateSampler(0.5)

        for i in range(10):
            span = tracer.trace(i)
            span.finish()

            samples = writer.pop()
            assert len(samples) <= 1, 'there should be 0 or 1 spans'
            sampled = (1 == len(samples))
            for j in range(10):
                other_span = Span(tracer, i, trace_id=span.trace_id)
                assert (
                    sampled == tracer.sampler.sample(other_span)
                ), 'sampling should give the same result for a given trace_id'


class RateByServiceSamplerTest(unittest.TestCase):
    def test_default_key(self):
        assert (
            'service:,env:' == RateByServiceSampler._default_key
        ), 'default key should correspond to no service and no env'

    def test_key(self):
        assert RateByServiceSampler._default_key == RateByServiceSampler._key()
        assert 'service:mcnulty,env:' == RateByServiceSampler._key(service='mcnulty')
        assert 'service:,env:test' == RateByServiceSampler._key(env='test')
        assert 'service:mcnulty,env:test' == RateByServiceSampler._key(service='mcnulty', env='test')
        assert 'service:mcnulty,env:test' == RateByServiceSampler._key('mcnulty', 'test')

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            tracer.configure(sampler=AllSampler())
            # We need to set the writer because tracer.configure overrides it,
            # indeed, as we enable priority sampling, we must ensure the writer
            # is priority sampling aware and pass it a reference on the
            # priority sampler to send the feedback it gets from the agent
            assert writer != tracer.writer, 'writer should have been updated by configure'
            tracer.writer = writer
            tracer.priority_sampler.set_sample_rate(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()
            samples_with_high_priority = 0
            for sample in samples:
                if sample.get_metric(SAMPLING_PRIORITY_KEY) is not None:
                    if sample.get_metric(SAMPLING_PRIORITY_KEY) > 0:
                        samples_with_high_priority += 1
                else:
                    assert (
                        0 == sample.get_metric(SAMPLING_PRIORITY_KEY)
                    ), 'when priority sampling is on, priority should be 0 when trace is to be dropped'

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) is None

            # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
            deviation = abs(samples_with_high_priority - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05, 'Deviation too high %f with sample_rate %f' % (deviation, sample_rate)

    def test_set_sample_rate_by_service(self):
        cases = [
            {
                'service:,env:': 1,
            },
            {
                'service:,env:': 1,
                'service:mcnulty,env:dev': 0.33,
                'service:postgres,env:dev': 0.7,
            },
            {
                'service:,env:': 1,
                'service:mcnulty,env:dev': 0.25,
                'service:postgres,env:dev': 0.5,
                'service:redis,env:prod': 0.75,
            },
        ]

        tracer = get_dummy_tracer()
        tracer.configure(sampler=AllSampler())
        priority_sampler = tracer.priority_sampler
        for case in cases:
            priority_sampler.set_sample_rate_by_service(case)
            rates = {}
            for k, v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates, '%s != %s' % (case, rates)
        # It's important to also test in reverse mode for we want to make sure key deletion
        # works as well as key insertion (and doing this both ways ensures we trigger both cases)
        cases.reverse()
        for case in cases:
            priority_sampler.set_sample_rate_by_service(case)
            rates = {}
            for k, v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates, '%s != %s' % (case, rates)
