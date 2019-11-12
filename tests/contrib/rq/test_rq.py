from ddtrace import Pin
from ddtrace.contrib.rq import patch, unpatch
from tests.base import BaseTracerTestCase
from tests.contrib.patch import PatchTestCase
from tests.subprocesstest import run_in_subprocess, SubprocessTestCase

# Note: in order to test the patching test cases we cannot import rq here


class RqPatchTestCase(PatchTestCase.Base):
    __integration_name__ = 'rq'
    __module_name__ = 'rq'
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, rq):
        # Job
        self.assert_wrapped(rq.job.Job.perform)
        self.assert_wrapped(rq.job.Job.fetch)

        # Queue
        assert rq.queue.Queue is rq.Queue
        self.assert_wrapped(rq.Queue.enqueue_job)
        self.assert_wrapped(rq.Queue.fetch_job)

        # Worker
        self.assert_wrapped(rq.worker.Worker.perform_job)

    def assert_not_module_patched(self, rq):
        # Job
        self.assert_not_wrapped(rq.Queue.enqueue_job)
        self.assert_not_wrapped(rq.Queue.fetch_job)

        # Queue
        self.assert_not_wrapped(rq.Queue.enqueue_job)
        self.assert_not_wrapped(rq.Queue.fetch_job)

        # Worker
        self.assert_not_wrapped(rq.worker.Worker.perform_job)

    def assert_not_module_double_patched(self, rq):
        # Job
        self.assert_not_double_wrapped(rq.job.Job.perform)
        self.assert_not_double_wrapped(rq.job.Job.fetch)

        # Queue
        self.assert_not_double_wrapped(rq.queue.Queue.enqueue_job)
        self.assert_not_double_wrapped(rq.queue.Queue.fetch_job)

        # Worker
        self.assert_not_double_wrapped(rq.worker.Worker.perform_job)


class TestRqConfig(BaseTracerTestCase, SubprocessTestCase):

    @run_in_subprocess
    def test_pin_installation(self):
        patch()
        import rq
        assert Pin.get_from(rq) is not None
        assert Pin.get_from(rq.job.Job) is not None
        assert Pin.get_from(rq.Queue) is not None
        assert Pin.get_from(rq.queue.Queue) is not None
        assert Pin.get_from(rq.Worker) is not None
        assert Pin.get_from(rq.worker.Worker) is not None
