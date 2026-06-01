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


def test_ml_job_env_parses_key_value_pairs():
    """DD_ML_JOB_ENV semicolon-separated pairs become DD_<KEY>=<VALUE> entries."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env("TRACE_ENABLED:true;AGENT_HOST:10.0.0.1")
    assert result == {"DD_TRACE_ENABLED": "true", "DD_AGENT_HOST": "10.0.0.1"}


def test_ml_job_env_value_may_contain_colons():
    """Only the first colon splits key from value; subsequent colons stay in the value."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env("AGENT_URL:http://localhost:8126")
    assert result == {"DD_AGENT_URL": "http://localhost:8126"}


def test_ml_job_env_strips_whitespace():
    """Leading/trailing whitespace around keys and values is stripped."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env(" TRACE_ENABLED : true ; AGENT_HOST : myhost ")
    assert result == {"DD_TRACE_ENABLED": "true", "DD_AGENT_HOST": "myhost"}


def test_ml_job_env_skips_malformed_pairs():
    """Pairs without a colon separator are silently skipped."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env("GOOD_KEY:val;no_separator;ANOTHER:ok")
    assert result == {"DD_GOOD_KEY": "val", "DD_ANOTHER": "ok"}


def test_ml_job_env_empty_string_returns_empty():
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    assert _parse_ml_job_env("") == {}


def test_ml_job_env_setdefault_does_not_override_explicit(monkeypatch):
    """Values already in env_vars are not overridden by DD_ML_JOB_ENV."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    env_vars = {"DD_TRACE_ENABLED": "true"}
    for k, v in _parse_ml_job_env("TRACE_ENABLED:false").items():
        env_vars.setdefault(k, v)

    assert env_vars["DD_TRACE_ENABLED"] == "true"
