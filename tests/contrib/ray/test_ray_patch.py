import ray.dashboard.modules.job.job_manager  # noqa: F401

from ddtrace.contrib.internal.ray.patch import get_version
from ddtrace.contrib.internal.ray.patch import patch
from ddtrace.contrib.internal.ray.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestRayPatch(PatchTestCase.Base):
    """Test Ray patching with default configuration (trace_core_api=False)"""

    __integration_name__ = "ray"
    __module_name__ = "ray"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, ray):
        self.assert_wrapped(ray.remote_function.RemoteFunction._remote)
        self.assert_wrapped(ray.dashboard.modules.job.job_manager.JobManager.submit_job)
        self.assert_wrapped(ray.dashboard.modules.job.job_manager.JobManager._monitor_job_internal)
        self.assert_wrapped(ray.actor._modify_class)
        self.assert_wrapped(ray.actor.ActorHandle._actor_method_call)
        self.assert_wrapped(ray.get)
        self.assert_wrapped(ray.wait)
        self.assert_wrapped(ray.put)

    def assert_not_module_patched(self, ray):
        self.assert_not_wrapped(ray.remote_function.RemoteFunction._remote)
        self.assert_not_wrapped(ray.dashboard.modules.job.job_manager.JobManager.submit_job)
        self.assert_not_wrapped(ray.dashboard.modules.job.job_manager.JobManager._monitor_job_internal)
        self.assert_not_wrapped(ray.actor._modify_class)
        self.assert_not_wrapped(ray.actor.ActorHandle._actor_method_call)
        self.assert_not_wrapped(ray.get)
        self.assert_not_wrapped(ray.wait)
        self.assert_not_wrapped(ray.put)

    def assert_not_module_double_patched(self, ray):
        self.assert_not_double_wrapped(ray.remote_function.RemoteFunction._remote)
        self.assert_not_double_wrapped(ray.dashboard.modules.job.job_manager.JobManager.submit_job)
        self.assert_not_double_wrapped(ray.dashboard.modules.job.job_manager.JobManager._monitor_job_internal)
        self.assert_not_double_wrapped(ray.actor._modify_class)
        self.assert_not_double_wrapped(ray.actor.ActorHandle._actor_method_call)
        self.assert_not_double_wrapped(ray.get)
        self.assert_not_double_wrapped(ray.wait)
        self.assert_not_double_wrapped(ray.put)
