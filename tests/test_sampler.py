from __future__ import division

import unittest
import random

from ddtrace.tracer import Tracer
from ddtrace.sampler import RateSampler, ThroughputSampler
from .test_tracer import DummyWriter
from .util import patch_time


class RateSamplerTest(unittest.TestCase):

    def test_random_sequence(self):
        writer = DummyWriter()
        sampler = RateSampler(0.5)
        tracer = Tracer(writer=writer)
        tracer.sampler = sampler

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

    def test_simple_limit(self):
        writer = DummyWriter()
        tracer = Tracer(writer=writer)

        with patch_time() as fake_time:

            tracer.sampler = ThroughputSampler(10, 2)

            for _ in range(15):
                s = tracer.trace("whatever")
                s.finish()
            traces = writer.pop()
            assert len(traces) == 10, "Wrong number of traces sampled, %s instead of %s" % (len(traces), 10)

            # Wait 3s to reset
            fake_time.sleep(3)

            for _ in range(15):
                s = tracer.trace("whatever")
                s.finish()
            traces = writer.pop()
            assert len(traces) == 10, "Wrong number of traces sampled, %s instead of %s" % (len(traces), 10)

    def test_sleep(self):
        writer = DummyWriter()
        tracer = Tracer(writer=writer)

        with patch_time() as fake_time:

            tracer.sampler = ThroughputSampler(10, 3)

            for _ in range(5):
                s = tracer.trace("whatever")
                s.finish()
            traces = writer.pop()
            assert len(traces) == 5, "Wrong number of traces sampled, %s instead of %s" % (len(traces), 5)

            # Less than the sampler period, but enough to change bucket
            fake_time.sleep(1)

            for _ in range(15):
                s = tracer.trace("whatever")
                s.finish()
            traces = writer.pop()
            assert len(traces) == 5, "Wrong number of traces sampled, %s instead of %s" % (len(traces), 5)

    def test_long_run(self):
        writer = DummyWriter()
        tracer = Tracer(writer=writer)

        with patch_time() as fake_time:
            limit = 100
            over = 5
            tracer.sampler = ThroughputSampler(limit, over)

            with patch_time() as fake_time:
                traces_per_s = 80
                total_time = 10
                for i in range(traces_per_s * total_time):
                    s = tracer.trace("whatever")
                    s.finish()
                    print s.sampled
                    if not (i + 1) % traces_per_s:
                        fake_time.sleep(1)

            traces = writer.pop()
            # We expect 100 traces, but the initialization of the current sampler implementation can introduce
            # an error of up-to `limit/over` traces
            got = len(traces)
            expected = (limit * total_time / over)
            error_delta = limit / over

            assert abs(got == expected) <= error_delta, \
                "Wrong number of traces sampled, %s instead of %s (error_delta > %s)" % (got, expected, error_delta)
