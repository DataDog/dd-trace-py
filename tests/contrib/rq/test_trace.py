import redis
import rq

from ddtrace import Pin
from ddtrace.contrib.rq import patch
from tests.base import BaseTracerTestCase


def job1(x):
    return x + 1


class TestRqTracingNotAsync(BaseTracerTestCase):
    """
    Test the rq integration with a non-async queue. This will execute jobs without
    a worker.
    """
    def setUp(self):
        super(TestRqTracingNotAsync, self).setUp()
        patch()
        self.r = redis.Redis()
        self.q = rq.Queue('queue-name', is_async=False, connection=self.r)
        Pin.override(rq, tracer=self.tracer)

    def test_enqueue(self):
        self.q.enqueue(job1, 1)
        spans = self.get_spans()
        assert len(spans) == 2

        span = spans[0]
        assert span.name == 'rq.queue.enqueue_job'
        assert span.service == 'rq'
        assert span.error == 0
        assert span.get_tag('job.id')
        assert span.get_tag('job.func_name') == 'tests.contrib.rq.test_trace.job1'
        assert span.get_tag('job.status') == 'finished'

        span = spans[1]
        assert span.name == 'rq.job.perform'
        assert span.service == 'rq-worker'
        assert span.error == 0
