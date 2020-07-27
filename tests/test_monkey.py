from ddtrace import monkey
from .subprocesstest import SubprocessTestCase, run_in_subprocess


class TestPatching(SubprocessTestCase):
    """
    In these test cases it is assumed that:

     1) redis is an integration that's available and is enabled by default
     2) httplib is an integration that's available and is disabled by default

     redis is used as a representative of enabled by default integrations.
     httplib is used as a representative of disabled by default integrations.
    """

    @run_in_subprocess(env_overrides=dict())
    def test_patch_all_env_override_redis_none(self):
        # Make sure redis is enabled by default.
        monkey.patch_all()
        assert "redis" in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_REDIS_ENABLED="false"))
    def test_patch_all_env_override_redis_disabled(self):
        monkey.patch_all()
        assert "redis" not in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_REDIS_ENABLED="false"))
    def test_patch_all_env_override_manual_patch(self):
        # Manual patching should not be affected by the environment variable override.
        monkey.patch(redis=True)
        assert "redis" in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict())
    def test_patch_all_env_override_httplib_none(self):
        # Make sure httplib is disabled by default.
        monkey.patch_all()
        assert "httplib" not in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_HTTPLIB_ENABLED="true"))
    def test_patch_all_env_override_httplib_enabled(self):
        monkey.patch_all()
        assert "httplib" in monkey._PATCHED_MODULES
