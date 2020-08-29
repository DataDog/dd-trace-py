from ddtrace import monkey
from tests.subprocesstest import SubprocessTestCase, run_in_subprocess


class TestPatching(SubprocessTestCase):
    """
    In these test cases it is assumed that:

     1) sqlite3 is an integration that's available and is enabled by default
     2) httplib is an integration that's available and is disabled by default

     sqlite3 is used as a representative of enabled by default integrations.
     httplib is used as a representative of disabled by default integrations.
    """

    @run_in_subprocess(env_overrides=dict())
    def test_patch_all_env_override_sqlite_none(self):
        # Make sure sqlite is enabled by default.
        monkey.patch_all()
        assert "sqlite3" in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_SQLITE3_ENABLED="false"))
    def test_patch_all_env_override_sqlite_disabled(self):
        monkey.patch_all()
        assert "sqlite3" not in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_SQLITE3_ENABLED="false"))
    def test_patch_all_env_override_manual_patch(self):
        # Manual patching should not be affected by the environment variable override.
        monkey.patch(sqlite3=True)
        assert "sqlite3" in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict())
    def test_patch_all_env_override_httplib_none(self):
        # Make sure httplib is disabled by default.
        monkey.patch_all()
        assert "httplib" not in monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_HTTPLIB_ENABLED="true"))
    def test_patch_all_env_override_httplib_enabled(self):
        monkey.patch_all()
        assert "httplib" in monkey._PATCHED_MODULES
