from ddtrace.contrib.rq import unpatch
from tests.base import BaseTracerTestCase
from tests.contrib.patch import PatchTestCase
from tests.subprocesstest import SubprocessTestCase


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


class TestRqConfig(BaseTracerTestCase, SubprocessTestCase):
    # Pin.override all the stuff
    pass
"""
