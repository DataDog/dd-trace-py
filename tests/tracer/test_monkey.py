from pathlib import Path
from unittest import TestCase
import unittest.mock

import pytest

from ddtrace import _monkey


class TestPatching(TestCase):
    """
    In these test cases it is assumed that:

     1) sqlite3 is an integration that's available and is enabled by default
     2) httplib is an integration that's available and is disabled by default

     sqlite3 is used as a representative of enabled by default integrations.
     httplib is used as a representative of disabled by default integrations.
    """

    def _scenario_test_patch_all_env_override_sqlite_none(self):
        # Make sure sqlite is enabled by default.
        _monkey._patch_all()
        assert "sqlite3" in _monkey._PATCHED_MODULES

    def _scenario_test_patch_all_env_override_sqlite_disabled(self):
        _monkey._patch_all()
        assert "sqlite3" not in _monkey._PATCHED_MODULES

    def _scenario_test_patch_all_env_override_manual_patch(self):
        # Manual patching should not be affected by the environment variable override.
        _monkey.patch(sqlite3=True)
        assert "sqlite3" in _monkey._PATCHED_MODULES

    def _scenario_test_patch_raise_exception_manual_patch(self):
        # Manual patching should not be affected by the environment variable override.
        with self.assertRaises(_monkey.ModuleNotFoundException) as me:
            _monkey.patch(module_dne=True)

        assert "module_dne does not have automatic instrumentation" in str(me.exception)
        assert "module_dne" not in _monkey._PATCHED_MODULES

    def _scenario_test_patch_no_raise_when_only_pyc_exists(self):
        # A compiled-only integration (patch.pyc, no patch.py) should not raise ModuleNotFoundException.
        original_exists = Path.exists

        def mock_exists(self):
            if self.name == "patch.py" and self.parent.name == "fake_pyc_mod":
                return False
            if self.name == "patch.pyc" and self.parent.name == "fake_pyc_mod":
                return True
            return original_exists(self)

        raised_module_not_found = False
        with unittest.mock.patch.object(Path, "exists", mock_exists):
            try:
                _monkey.patch(raise_errors=True, fake_pyc_mod=True)
            except _monkey.ModuleNotFoundException:
                raised_module_not_found = True
            except Exception:
                pass  # ImportError etc. for non-existent fake module is expected

        assert not raised_module_not_found, "ModuleNotFoundException raised when patch.pyc exists"

    def _scenario_test_patch_all_env_override_httplib_none(self):
        # Make sure httplib is disabled by default.
        _monkey._patch_all()
        assert "httplib" not in _monkey._PATCHED_MODULES

    def _scenario_test_patch_all_env_override_httplib_enabled(self):
        _monkey._patch_all()
        assert "httplib" in _monkey._PATCHED_MODULES

    def _scenario_test_patch_all_env_override_model_lab_enables_mlflow(self):
        _monkey._patch_all()
        assert "mlflow" in _monkey._PATCHED_MODULES

    def _scenario_test_patch_exception_on_import(self):
        # Manual patching should not be affected by the environment variable override.
        import sqlite3

        del sqlite3.connect

        with self.assertRaises(AttributeError):
            _monkey.patch(raise_errors=True, sqlite3=True)

    def _scenario_test_patch_exception_on_import_no_raise(self):
        # Manual patching should not be affected by the environment variable override.
        import sqlite3

        del sqlite3.connect

        _monkey.patch(raise_errors=False, sqlite3=True)

    def _scenario_test_patch_guardrails_with_unsupported_integration_version(self):
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
                    "Skipped patching 'fastapi' integration, installed version: 0.0.0 is not compatible "
                    + "with integration support spec: %s." % fastapi_supported_version,
                    version="0.0.0",
                )
                mock_add_integration.assert_any_call(
                    "sqlite3",
                    True,
                    True,
                    "",
                    version="3.46.1",
                )


@pytest.mark.subprocess(env=dict(), out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_all_env_override_sqlite_none():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_all_env_override_sqlite_none") as case:
        case._scenario_test_patch_all_env_override_sqlite_none()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SQLITE3_ENABLED="false"), out=None, err=None, command=True, test_assertions=True
)
def test_TestPatching_patch_all_env_override_sqlite_disabled():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_all_env_override_sqlite_disabled") as case:
        case._scenario_test_patch_all_env_override_sqlite_disabled()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SQLITE3_ENABLED="false"), out=None, err=None, command=True, test_assertions=True
)
def test_TestPatching_patch_all_env_override_manual_patch():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_all_env_override_manual_patch") as case:
        case._scenario_test_patch_all_env_override_manual_patch()


@pytest.mark.subprocess(env={}, out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_raise_exception_manual_patch():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_raise_exception_manual_patch") as case:
        case._scenario_test_patch_raise_exception_manual_patch()


@pytest.mark.subprocess(env={}, out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_no_raise_when_only_pyc_exists():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_no_raise_when_only_pyc_exists") as case:
        case._scenario_test_patch_no_raise_when_only_pyc_exists()


@pytest.mark.subprocess(env=dict(), out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_all_env_override_httplib_none():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_all_env_override_httplib_none") as case:
        case._scenario_test_patch_all_env_override_httplib_none()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_HTTPLIB_ENABLED="true"), out=None, err=None, command=True, test_assertions=True
)
def test_TestPatching_patch_all_env_override_httplib_enabled():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_all_env_override_httplib_enabled") as case:
        case._scenario_test_patch_all_env_override_httplib_enabled()


@pytest.mark.subprocess(env=dict(DD_MODEL_LAB_ENABLED="true"), out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_all_env_override_model_lab_enables_mlflow():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_all_env_override_model_lab_enables_mlflow") as case:
        case._scenario_test_patch_all_env_override_model_lab_enables_mlflow()


@pytest.mark.subprocess(env={}, out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_exception_on_import():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_exception_on_import") as case:
        case._scenario_test_patch_exception_on_import()


@pytest.mark.subprocess(env={}, out=None, err=None, command=True, test_assertions=True)
def test_TestPatching_patch_exception_on_import_no_raise():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_exception_on_import_no_raise") as case:
        case._scenario_test_patch_exception_on_import_no_raise()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SAFE_INSTRUMENTATION_ENABLED="true"), out=None, err=None, command=True, test_assertions=True
)
def test_TestPatching_patch_guardrails_with_unsupported_integration_version():
    from tests.process import case_context
    from tests.tracer.test_monkey import TestPatching

    with case_context(TestPatching, "_scenario_test_patch_guardrails_with_unsupported_integration_version") as case:
        case._scenario_test_patch_guardrails_with_unsupported_integration_version()
