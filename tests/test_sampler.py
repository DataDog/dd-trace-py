from __future__ import division

import unittest
import random
import time
import threading

from ddtrace.tracer import Tracer
from ddtrace.span import Span
from ddtrace.sampler import RateSampler, SAMPLE_RATE_METRIC_KEY
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
                tracer.sampler.sample(other_span)
                assert sampled == other_span.sampled, "sampling should give the same result for a given trace_id"
