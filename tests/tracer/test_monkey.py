import unittest.mock

from ddtrace import _monkey
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


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
        _monkey._patch_all()
        assert "sqlite3" in _monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_SQLITE3_ENABLED="false"))
    def test_patch_all_env_override_sqlite_disabled(self):
        _monkey._patch_all()
        assert "sqlite3" not in _monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_SQLITE3_ENABLED="false"))
    def test_patch_all_env_override_manual_patch(self):
        # Manual patching should not be affected by the environment variable override.
        _monkey.patch(sqlite3=True)
        assert "sqlite3" in _monkey._PATCHED_MODULES

    @run_in_subprocess()
    def test_patch_raise_exception_manual_patch(self):
        # Manual patching should not be affected by the environment variable override.
        with self.assertRaises(_monkey.ModuleNotFoundException) as me:
            _monkey.patch(module_dne=True)

        assert "module_dne does not have automatic instrumentation" in str(me.exception)
        assert "module_dne" not in _monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict())
    def test_patch_all_env_override_httplib_none(self):
        # Make sure httplib is disabled by default.
        _monkey._patch_all()
        assert "httplib" not in _monkey._PATCHED_MODULES

    @run_in_subprocess(env_overrides=dict(DD_TRACE_HTTPLIB_ENABLED="true"))
    def test_patch_all_env_override_httplib_enabled(self):
        _monkey._patch_all()
        assert "httplib" in _monkey._PATCHED_MODULES

    @run_in_subprocess()
    def test_patch_exception_on_import(self):
        # Manual patching should not be affected by the environment variable override.
        import sqlite3

        del sqlite3.connect

        with self.assertRaises(AttributeError):
            _monkey.patch(raise_errors=True, sqlite3=True)

    @run_in_subprocess()
    def test_patch_exception_on_import_no_raise(self):
        # Manual patching should not be affected by the environment variable override.
        import sqlite3

        del sqlite3.connect

        _monkey.patch(raise_errors=False, sqlite3=True)

    @run_in_subprocess(env_overrides=dict(DD_TRACE_SAFE_INSTRUMENTATION_ENABLED="true"))
    def test_patch_guardrails_with_unsupported_integration_version(self):
        # spy on fastapi get_version method and return a fake version we don't support
        with unittest.mock.patch("ddtrace.contrib.internal.fastapi.patch.get_version", return_value="0.0.0"):
            # Spy on the telemetry writer to ensure that the integration is reported as not patched.
            with unittest.mock.patch(
                "ddtrace.internal.telemetry.telemetry_writer.add_integration"
            ) as mock_add_integration:
                import ddtrace

                _monkey._patch_all()

                fastapi_supported_version = ddtrace.contrib.internal.fastapi.patch._supported_versions().get("fastapi")


                mock_add_integration.assert_any_call(
                    "fastapi",
                    False,
                    True,
                    "Unable to patch integration: fastapi, installed version: 0.0.0 is not compatible with "
                    + "integration support spec of %s" % fastapi_supported_version,
                    version="0.0.0",
                )
                mock_add_integration.assert_any_call(
                    "sqlite3",
                    True,
                    True,
                    "",
                    version="3.40.1",
                )
