from __future__ import division

import unittest
import random
import time
import threading

from ddtrace.tracer import Tracer
from ddtrace.span import Span
from ddtrace.sampler import RateSampler, ThroughputSampler, SAMPLE_RATE_METRIC_KEY
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

class ThroughputSamplerTest(unittest.TestCase):
    """Test suite for the ThroughputSampler"""

    def test_simple_limit(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        with patch_time() as fake_time:
            tps = 5
            tracer.sampler = ThroughputSampler(tps)

            for _ in range(10):
                s = tracer.trace("whatever")
                s.finish()
            traces = writer.pop()

            got = len(traces)
            expected = 10

            assert got == expected, \
                "Wrong number of traces sampled, %s instead of %s" % (got, expected)

            # Wait enough to reset
            fake_time.sleep(tracer.sampler.BUFFER_DURATION + 1)

            for _ in range(100):
                s = tracer.trace("whatever")
                s.finish()
            traces = writer.pop()

            got = len(traces)
            expected = tps * tracer.sampler.BUFFER_DURATION

            assert got == expected, \
                "Wrong number of traces sampled, %s instead of %s" % (got, expected)

    def test_long_run(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # Test a big matrix of combinaisons
        # Ensure to have total_time >> BUFFER_DURATION to reduce edge effects
        for tps in [10, 23, 15, 31]:
            for (traces_per_s, total_time) in [(80, 23), (75, 66), (1000, 77)]:

                with patch_time() as fake_time:
                    # We do tons of operations in this test, do not let the time slowly shift
                    fake_time.set_delta(0)

                    tracer.sampler = ThroughputSampler(tps)

                    for _ in range(total_time):
                        for _ in range(traces_per_s):
                            s = tracer.trace("whatever")
                            s.finish()
                        fake_time.sleep(1)

                traces = writer.pop()
                # The current sampler implementation can introduce an error of up to
                # `tps * BUFFER_DURATION` traces at initialization (since the sampler starts empty)
                got = len(traces)
                expected = tps * total_time
                error_delta = tps * tracer.sampler.BUFFER_DURATION

                assert abs(got - expected) <= error_delta, \
                    "Wrong number of traces sampled, %s instead of %s (error_delta > %s)" % (got, expected, error_delta)

    def test_concurrency(self):
        # Test that the sampler works well when used in different threads
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        total_time = 3
        concurrency = 100
        end_time = time.time() + total_time

        # Let's sample to a multiple of BUFFER_SIZE, so that we can pre-populate the buffer
        tps = 15 * ThroughputSampler.BUFFER_SIZE
        tracer.sampler = ThroughputSampler(tps)

        threads = []

        def run_simulation(tracer, end_time):
            while time.time() < end_time:
                s = tracer.trace("whatever")
                s.finish()
                # ~1000 traces per s per thread
                time.sleep(0.001)

        for i in range(concurrency):
            thread = threading.Thread(target=run_simulation, args=(tracer, end_time))
            threads.append(thread)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        traces = writer.pop()

        got = len(traces)
        expected = tps * total_time
        error_delta = tps * ThroughputSampler.BUFFER_DURATION

        assert abs(got - expected) <= error_delta, \
            "Wrong number of traces sampled, %s instead of %s (error_delta > %s)" % (got, expected, error_delta)
