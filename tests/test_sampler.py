from __future__ import division

import unittest
import random
import time
import threading

from ddtrace.tracer import Tracer
from ddtrace.sampler import RateSampler, ThroughputSampler
from .test_tracer import DummyWriter
from .util import patch_time


class RateSamplerTest(unittest.TestCase):

    def test_random_sequence(self):
        writer = DummyWriter()
        sampler = RateSampler(0.5)
        tracer = Tracer(writer=writer, sampler=sampler)

        # Set the seed so that the choice of sampled traces is deterministic, then write tests accordingly
        random.seed(4012)

        # First trace, sampled
        with tracer.trace("foo") as s:
            assert s.sampled
            assert s.weight == 2
        assert writer.pop()

        # Second trace, not sampled
        with tracer.trace("figh") as s:
            assert not s.sampled
            s2 = tracer.trace("what")
            assert not s2.sampled
            s2.finish()
            with tracer.trace("ever") as s3:
                assert not s3.sampled
                s4 = tracer.trace("!")
                assert not s4.sampled
                s4.finish()
        spans = writer.pop()
        assert not spans, spans

        # Third trace, not sampled
        with tracer.trace("ters") as s:
            assert s.sampled
        assert writer.pop()


class ThroughputSamplerTest(unittest.TestCase):
    """Test suite for the ThroughputSampler"""

    def test_simple_limit(self):
        writer = DummyWriter()
        tracer = Tracer(writer=writer)

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
        tracer = Tracer(writer=writer)

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
        tracer = Tracer(writer=writer)

        total_time = 10
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
