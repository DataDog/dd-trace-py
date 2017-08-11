from __future__ import division

import unittest
import random

from ddtrace.tracer import Tracer
from ddtrace.sampler import RateSampler, DistributedSampled, SAMPLE_RATE_METRIC_KEY
from .test_tracer import DummyWriter


class DistributedRateSamplerTest(unittest.TestCase):

    def test_distributed_sample_rate_deviation(self):
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

            # Less than 2% deviation when "enough" iterations (arbitrary, just check if it converges)
            deviation = abs(distributed_sampled - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.02, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

    def test_statistic_behavior(self):
        """ Test that when there's no trace ID, the result is really random """
        writer = DummyWriter()

        tracer = Tracer()
        tracer.writer = writer

        sample_rate = 0.42
        tracer.sampler = RateSampler(sample_rate)

        random.seed(1234)

        iterations = int(1e4)
        sampled = 0
        pseudo_span = DistributedSampled()

        for i in range(iterations):
            tracer.sampler.sample(pseudo_span)
            if pseudo_span.sampled:
                sampled += 1

        # Less than 2% deviation when "enough" iterations (arbitrary, just check if it converges)
        deviation = abs(sampled - (iterations * sample_rate)) / (iterations * sample_rate)
        assert deviation < 0.02, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

class DistributedCombinedRateSamplerTest(unittest.TestCase):

    def test_distributed_combined_sample_rate_deviation(self):
        writer = DummyWriter()

        for sample_rate in [0.3, 0.7]:
            for distributed_sample_rate in [0.3, 0.7]:
                tracer = Tracer()
                tracer.writer = writer

                tracer.sampler = RateSampler(sample_rate)
                tracer.distributed_sampler = RateSampler(distributed_sample_rate)

                # combined_sample_rate is the product of both rates, this means that distributed sampling
                # is applied on top of regular sampling. This means if we have 50% regular sampling
                # and 30% distributed sampling, then only 15% of all traces are going to be marked
                # as "should be sampled".
                combined_sample_rate = sample_rate * distributed_sample_rate

                random.seed(1234)

                iterations = int(1e4 / combined_sample_rate)

                for i in range(iterations):
                    span = tracer.trace(i)
                    span.finish()

                samples = writer.pop()

                # We must have at least 1 sample, check that it has its sample rate properly assigned
                assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) == sample_rate

                # Less than 2% deviation when "enough" iterations (arbitrary, just check if it converges)
                deviation = abs(len(samples) - (iterations * sample_rate)) / (iterations * sample_rate)
                assert deviation < 0.02, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

                distributed_sampled = 0
                for span in samples:
                    if span.distributed.sampled:
                        distributed_sampled += 1

                # Less than 2% deviation when "enough" iterations (arbitrary, just check if it converges)
                distributed_deviation = abs(distributed_sampled - (iterations * combined_sample_rate)) / \
                    (iterations * combined_sample_rate)
                assert distributed_deviation < 0.02, \
                    "Deviation too high %f with sample_rate %f, distributed_sample_rate %f, combined_sample_rate %f" % \
                    (distributed_deviation, sample_rate, distributed_sample_rate, combined_sample_rate)

# [TODO:christian] write tests & check what happens with ThroughputSampler
