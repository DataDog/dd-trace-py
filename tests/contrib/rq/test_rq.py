from ddtrace import Pin
from ddtrace.contrib.rq import patch, unpatch
from tests.base import BaseTracerTestCase
from tests.contrib.patch import PatchTestCase
from tests.subprocesstest import SubprocessTestCase, run_in_subprocess


"""
class RqPatchTestCase(PatchTestCase.Base):
    __integration_name__ = 'rq'
    __module_name__ = 'rq'
    __unpatch_func__ = unpatch

    def assert_module_patched(self, rq):
        self.assert_wrapped(rq.queue.Queue.enqueue_job)
        self.assert_wrapped(rq.Queue.enqueue_job)

    def assert_not_module_patched(self, rq):
        self.assert_not_wrapped(rq.queue.Queue.enqueue_job)
        self.assert_not_wrapped(rq.Queue.enqueue_job)

    def assert_not_module_double_patched(self, rq):
        self.assert_not_double_wrapped(rq.queue.Queue.enqueue_job)

"""


class TestRqConfig(BaseTracerTestCase, SubprocessTestCase):
    # Pin.override all the stuff

    @run_in_subprocess
    def test_pin_installation(self):
        patch()
        import rq
        assert Pin.get_from(rq) is not None
        assert Pin.get_from(rq.job.Job) is not None
        assert Pin.get_from(rq.Queue) is not None
        assert Pin.get_from(rq.queue.Queue) is not None
        assert Pin.get_from(rq.Worker) is not None

