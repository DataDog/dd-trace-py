from __future__ import division

import unittest
import random

from ddtrace.tracer import Tracer
from ddtrace.sampler import RateSampler, SAMPLE_RATE_METRIC_KEY
from .test_tracer import DummyWriter


class DistributedRateSamplerTest(unittest.TestCase):

    def test_distributed_sampler_rate_deviation(self):
        writer = DummyWriter()

        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = Tracer()
            tracer.writer = writer

            tracer.distributed_sampler = RateSampler(sample_rate)

            random.seed(1234)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()

            assert len(samples) == iterations, "all traces have been sampled by regular sampler"

            distributed_sampled = 0
            for span in samples:
                if span.distributed.sampled:
                    distributed_sampled += 1

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) is None, "no regular sampling"

            # Less than 1% deviation when "enough" iterations (arbitrary, just check if it converges)
            deviation = abs(distributed_sampled - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.02, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)
