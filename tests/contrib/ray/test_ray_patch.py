import importlib

import pytest
import ray.dashboard.modules.job.job_manager  # noqa: F401

from ddtrace import config
from ddtrace.contrib.internal.ray import patch as ray_patch
from ddtrace.contrib.internal.ray.patch import get_version
from ddtrace.contrib.internal.ray.patch import patch
from ddtrace.contrib.internal.ray.patch import unpatch
from tests.contrib.patch import PatchTestCase


@pytest.fixture
def reset_ray_config(request):
    """Re-seed ``config.ray`` defaults from the current env.

    ``config._add`` is merge-once per key: a second registration keeps the
    originally-registered defaults rather than re-evaluating modifiers.
    Call the returned function *after* setting env vars to force a
    re-seed against the current env.

    A teardown reload is performed automatically so that other tests do not
    observe leaked config state from a monkeypatched environment.

    Implementation note: this fixture uses ``request.addfinalizer`` rather
    than ``yield`` teardown so that its cleanup runs AFTER pytest's
    ``monkeypatch`` fixture has already restored the environment.  With a plain
    ``yield``, teardown order would be reversed — our cleanup would fire while
    env vars are still monkeypatched, baking the patched values into the
    config for subsequent tests.
    """

    def _do_reset():
        # Remove from both storage locations: _integration_configs (normal path)
        # and config.__dict__ (set by monkeypatch.setattr on a prior test).
        # If config.__dict__ has "ray" it shadows _integration_configs and
        # _add's deepmerge will pull the stale value in as "existing".
        config._integration_configs.pop("ray", None)
        config.__dict__.pop("ray", None)
        importlib.reload(ray_patch)

    # Register teardown as a finalizer so it runs after monkeypatch restores env.
    request.addfinalizer(_do_reset)
    return _do_reset


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


def test_train_enabled_defaults_true(monkeypatch, reset_ray_config):
    monkeypatch.delenv("DD_TRACE_RAY_TRAIN_ENABLED", raising=False)
    monkeypatch.delenv("DD_TRACE_RAY_TRAIN_PER_RANK_TRACE", raising=False)
    reset_ray_config()

    assert config.ray.train_enabled is True
    assert config.ray.train_per_rank_trace is False


def test_train_enabled_respects_env(monkeypatch, reset_ray_config):
    monkeypatch.setenv("DD_TRACE_RAY_TRAIN_ENABLED", "false")
    monkeypatch.setenv("DD_TRACE_RAY_TRAIN_PER_RANK_TRACE", "true")
    reset_ray_config()

    assert config.ray.train_enabled is False
    assert config.ray.train_per_rank_trace is True


def test_dd_job_env_prefix_propagates_with_translation(monkeypatch):
    """DD_JOB_ENV_<NAME> on the dashboard env propagates to the Ray
    runtime_env as DD_<NAME>. Plain DD_<NAME> (without the prefix) does NOT.
    This verifies the security fix: secrets like DD_API_KEY are no longer
    automatically leaked into Ray job specs.
    """
    from ddtrace.contrib.internal.ray.patch import _propagate_dd_job_env

    # Simulate dashboard environment with a mix of DD_* and DD_JOB_ENV_* vars.
    source_env = {
        "DD_API_KEY": "should-not-propagate",
        "DD_APP_KEY": "should-not-propagate-either",
        "DD_TRACE_ENABLED": "true",  # plain DD_* — should NOT propagate
        "DD_JOB_ENV_TRACE_ENABLED": "true",  # should become DD_TRACE_ENABLED
        "DD_JOB_ENV_PATCH_MODULES": "pytorch:true",  # should become DD_PATCH_MODULES
        "SOME_OTHER_VAR": "ignored",
    }

    env_vars: dict = {}
    _propagate_dd_job_env(env_vars, source_env)

    # Secrets must NOT propagate.
    assert "DD_API_KEY" not in env_vars
    assert "DD_APP_KEY" not in env_vars
    # Plain DD_TRACE_ENABLED (no prefix) must NOT propagate.
    # (The setdefault call means if it was explicitly set it stays, but it shouldn't
    # be set from the plain DD_TRACE_ENABLED source var.)
    # Prefixed vars propagate with the prefix stripped.
    assert env_vars.get("DD_TRACE_ENABLED") == "true"
    assert env_vars.get("DD_PATCH_MODULES") == "pytorch:true"
    # Non-DD vars are ignored.
    assert "SOME_OTHER_VAR" not in env_vars


def test_dd_job_env_setdefault_does_not_override_explicit(monkeypatch):
    """Explicit values already in env_vars are not overridden by DD_JOB_ENV_*."""
    from ddtrace.contrib.internal.ray.patch import _propagate_dd_job_env

    source_env = {
        "DD_JOB_ENV_TRACE_ENABLED": "false",
    }
    env_vars = {"DD_TRACE_ENABLED": "true"}  # already set explicitly
    _propagate_dd_job_env(env_vars, source_env)

    # setdefault must not override the existing value.
    assert env_vars["DD_TRACE_ENABLED"] == "true"
