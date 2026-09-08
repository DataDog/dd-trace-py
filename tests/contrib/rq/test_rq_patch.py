from ddtrace.contrib.internal.rq.patch import get_version
from ddtrace.contrib.internal.rq.patch import patch
from ddtrace.contrib.internal.rq.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestRQPatch(PatchTestCase.Base):
    __integration_name__ = "rq"
    __module_name__ = "rq"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def _assert_patch_state(self, assertion, rq):
        assertion(rq.job.Job.perform)
        assertion(rq.queue.Queue.enqueue_job)
        assertion(rq.queue.Queue.fetch_job)
        assertion(rq.Worker.perform_job)
        assertion(rq.SimpleWorker.perform_job)

    def assert_module_patched(self, rq):
        self._assert_patch_state(self.assert_wrapped, rq)

    def assert_not_module_patched(self, rq):
        self._assert_patch_state(self.assert_not_wrapped, rq)

    def assert_not_module_double_patched(self, rq):
        self._assert_patch_state(self.assert_not_double_wrapped, rq)
