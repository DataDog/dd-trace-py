import os
import subprocess
import time

import pytest
import redis

import rq

from ddtrace import Pin
from ddtrace.contrib.rq import patch, unpatch
from tests import TracerTestCase, snapshot, override_config

from .jobs import job_add1, job_fail

snapshot_ignores = ["meta.job.id", "meta.error.stack"]


class TestRQTracingSync(TracerTestCase):
    """
    Test the rq integration with a non-async queue. This will execute jobs
    without a worker.
    """

    def setUp(self):
        super(TestRQTracingSync, self).setUp()
        patch()
        self.r = redis.Redis()
        self.sync_q = rq.Queue("sync-q", is_async=False, connection=self.r)
        self.q = rq.Queue("q", connection=self.r)

    def tearDown(self):
        unpatch()

    @snapshot(ignores=snapshot_ignores)
    def test_queue_enqueue(self):
        self.sync_q.enqueue(job_add1, 1)

    @snapshot(ignores=snapshot_ignores)
    def test_queue_failing_job(self):
        with pytest.raises(Exception):
            self.sync_q.enqueue(job_fail)

    @snapshot(ignores=snapshot_ignores)
    def test_sync_worker(self):
        job = self.q.enqueue(job_add1, 1)
        worker = rq.SimpleWorker([self.q], connection=self.q.connection)
        worker.work(burst=True)
        assert job.result == 2

    @snapshot(ignores=snapshot_ignores)
    def test_sync_worker_multiple_jobs(self):
        jobs = []
        for i in range(3):
            jobs.append(self.q.enqueue(job_add1, i))
        worker = rq.SimpleWorker([self.q], connection=self.q.connection)
        worker.work(burst=True)
        assert [job.result for job in jobs] == [1, 2, 3]

    @snapshot(ignores=snapshot_ignores)
    def test_sync_worker_config_service(self):
        job = self.q.enqueue(job_add1, 10)
        with override_config("rq_worker", dict(service="my-worker-svc")):
            worker = rq.SimpleWorker([self.q], connection=self.q.connection)
            worker.work(burst=True)
        assert job.result == 11

    @snapshot(ignores=snapshot_ignores)
    def test_sync_worker_pin_service(self):
        job = self.q.enqueue(job_add1, 10)
        worker = rq.SimpleWorker([self.q], connection=self.q.connection)
        Pin.override(worker, service="my-pin-svc")
        worker.work(burst=True)
        assert job.result == 11

    @snapshot(ignores=snapshot_ignores)
    def test_worker_failing_job(self):
        self.q.enqueue(job_fail)
        worker = rq.SimpleWorker([self.q], connection=self.q.connection)
        worker.work(burst=True)


class TestRQWorker(TracerTestCase):
    """
    Run test cases with a real rq worker.
    """

    def setUp(self):
        super(TestRQWorker, self).setUp()
        patch()
        self.r = redis.Redis()
        self.q = rq.Queue(connection=self.r)

    def tearDown(self):
        unpatch()

    @snapshot(ignores=snapshot_ignores, async_mode=False)
    def test_enqueue(self):
        p = subprocess.Popen(["ddtrace-run", "rq", "worker"], env=dict(**os.environ, DD_TRACE_REDIS_ENABLED="false"))
        try:
            job = self.q.enqueue(job_add1, 1)
            # Wait for traces to be flushed from the worker.
            while job.result is None:
                time.sleep(0.01)
            assert job.result == 2
        finally:
            p.terminate()
        time.sleep(0.3)
