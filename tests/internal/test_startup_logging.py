import os
import subprocess
import sys
import textwrap

import pytest


def run(code, **env):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env=dict(os.environ, **env),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    return result


@pytest.mark.parametrize("telemetry", ["true", "false"])
def test_logger_import_and_setup_do_not_start_telemetry(telemetry):
    run(
        """
        import logging
        import sys
        from ddtrace._logger import configure_ddtrace_logger
        assert 'ddtrace.internal.native' not in sys.modules
        handlers = list(logging.getLogger('ddtrace').handlers)
        assert len(handlers) == 1
        configure_ddtrace_logger()
        assert logging.getLogger('ddtrace').handlers == handlers
        assert 'ddtrace.internal.native' not in sys.modules
        assert 'ddtrace.internal.forksafe' not in sys.modules
        assert 'ddtrace.internal.telemetry' not in sys.modules
        assert 'ddtrace.internal.settings._config' not in sys.modules
        assert 'ddtrace.trace' not in sys.modules
    """,
        DD_INSTRUMENTATION_TELEMETRY_ENABLED=telemetry,
    )


@pytest.mark.parametrize("level", [None, "WARNING"])
@pytest.mark.parametrize("fleet_enabled", [False, True])
def test_logging_configuration_ignores_stable_configs(tmp_path, level, fleet_enabled):
    local = tmp_path / "local.yaml"
    fleet = tmp_path / "fleet.yaml"
    local.write_text('apm_configuration_default:\n  DD_TRACE_LOG_LEVEL: "INFO"\n')
    if fleet_enabled:
        fleet.write_text('config_id: "logging-config"\napm_configuration_default:\n  DD_TRACE_LOG_LEVEL: "ERROR"\n')
    run(
        f"""
        import logging
        import os
        import sys
        os.environ.pop('DD_TRACE_LOG_LEVEL', None)
        level = {level!r}
        if level is not None:
            os.environ['DD_TRACE_LOG_LEVEL'] = {level!r}
        import ddtrace
        from ddtrace._logger import _configurations
        assert logging.getLogger('ddtrace').level == {"logging.WARNING" if level else "logging.NOTSET"}
        assert _configurations['DD_TRACE_LOG_LEVEL'] == ({level!r}, {"env_var" if level else "default"!r})
        assert 'ddtrace.internal.native' not in sys.modules
        from ddtrace.internal.settings._core import FLEET_CONFIG, LOCAL_CONFIG
        if {fleet_enabled}:
            assert FLEET_CONFIG['DD_TRACE_LOG_LEVEL'] == 'ERROR'
        else:
            assert LOCAL_CONFIG['DD_TRACE_LOG_LEVEL'] == 'INFO'
        assert logging.getLogger('ddtrace').level == {"logging.WARNING" if level else "logging.NOTSET"}
    """,
        _DD_SC_LOCAL_FILE_OVERRIDE=str(local),
        _DD_SC_MANAGED_FILE_OVERRIDE=str(fleet),
        DD_TRACE_DEBUG="false",
    )


def test_logging_configuration_is_replayed_to_telemetry():
    run(
        """
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        import ddtrace
        writer = Mock()
        sys.modules['ddtrace.internal.telemetry.noop_writer'] = SimpleNamespace(
            NoOpTelemetryWriter=Mock(return_value=writer)
        )
        assert 'ddtrace.internal.telemetry' not in sys.modules
        import ddtrace.internal.telemetry
        writer.add_configuration.assert_any_call('DD_TRACE_LOG_LEVEL', 'INFO', 'env_var')
        writer.add_configuration.assert_any_call('_DD_NATIVE_LOGGING_BACKEND', None, 'default')
    """,
        DD_TRACE_LOG_LEVEL="INFO",
        DD_TRACE_DEBUG="false",
        DD_INSTRUMENTATION_TELEMETRY_ENABLED="false",
    )


def test_native_logging_waits_for_native_import(tmp_path):
    logfile = tmp_path / "native.log"
    run(
        f"""
        import sys
        from pathlib import Path
        import ddtrace
        assert 'ddtrace.internal.native' not in sys.modules
        assert not Path({str(logfile)!r}).exists()
        from ddtrace.internal.native import logger
        logger.log('warning', 'native logging enabled')
        logger.disable('file')
        assert 'native logging enabled' in Path({str(logfile)!r}).read_text()
        assert 'ddtrace.internal.telemetry' not in sys.modules
        assert 'ddtrace.trace' not in sys.modules
    """,
        _DD_NATIVE_LOGGING_BACKEND="file",
        _DD_NATIVE_LOGGING_FILE_PATH=str(logfile),
    )


@pytest.mark.parametrize(
    "entrypoint",
    [
        "import ddtrace.auto",
        "from ddtrace.trace import tracer",
        "from ddtrace import patch; patch()",
        "from ddtrace import config",
    ],
)
def test_logger_configured_once_across_entrypoints(tmp_path, entrypoint):
    run(
        f"""
        import logging
        from concurrent.futures import ThreadPoolExecutor
        from ddtrace._logger import configure_ddtrace_logger
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: configure_ddtrace_logger(), range(20)))
        handlers = list(logging.getLogger('ddtrace').handlers)
        assert len(handlers) == 2, handlers
        {entrypoint}
        assert logging.getLogger('ddtrace').handlers == handlers
    """,
        DD_TRACE_LOG_FILE=str(tmp_path / "trace.log"),
        DD_TRACE_LOG_STREAM_HANDLER="true",
    )


@pytest.mark.parametrize(
    "entrypoint",
    [
        "import ddtrace.auto",
        "from ddtrace.trace import tracer",
        "from ddtrace import patch; patch()",
        "from ddtrace import config",
    ],
)
def test_invalid_logging_configuration_does_not_abort_setup(entrypoint):
    result = run(
        f"""
        {entrypoint}
        from ddtrace._logger import _configured
        assert _configured
    """,
        DD_TRACE_LOG_FILE_LEVEL="INVALID",
    )
    assert b"Failed to configure ddtrace logging" in result.stderr


@pytest.mark.parametrize("telemetry", ["true", "false"])
def test_public_config_warnings_use_configured_logger(tmp_path, telemetry):
    log_file = tmp_path / "trace.log"
    result = run(
        """
        import sys
        from ddtrace import config
        assert config._debug_mode is False
        assert 'ddtrace.trace' not in sys.modules
    """,
        DD_TRACE_LOG_FILE=str(log_file),
        DD_TRACE_LOG_LEVEL="WARNING",
        DD_TRACE_DEBUG="false",
        OTEL_LOG_LEVEL="warning",
        DD_INSTRUMENTATION_TELEMETRY_ENABLED=telemetry,
    )
    warning = "Setting OTEL_LOG_LEVEL to warning is not supported by ddtrace, this configuration will be ignored."
    assert result.stderr.decode().count(warning) == 1
    assert log_file.read_text().count(warning) == 1


@pytest.mark.parametrize(
    "version, message", [((3, 8), "was removed in 4.0.0"), ((3, 9), "will be removed in 5.0.0"), ((3, 10), None)]
)
def test_python_version_warning(version, message):
    run(f"""
        import sys
        import warnings
        from unittest.mock import patch
        from ddtrace.internal.utils.deprecations import check_supported_python_version, DDTraceDeprecationWarning
        with patch.object(sys, 'version_info', {version}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                check_supported_python_version()
        if {message!r} is None:
            assert not caught
        else:
            assert len(caught) == 1
            assert caught[0].category is DDTraceDeprecationWarning
            assert {message!r} in str(caught[0].message)
        assert 'ddtrace.internal.telemetry' not in sys.modules
        assert 'ddtrace.internal.native' not in sys.modules
    """)


@pytest.mark.parametrize("telemetry", ["true", "false"])
def test_core_import_does_not_reconfigure_logging(telemetry):
    run(
        """
        import sys
        import logging
        import ddtrace
        handlers = list(logging.getLogger('ddtrace').handlers)
        import ddtrace.internal.core
        assert logging.getLogger('ddtrace').handlers[:len(handlers)] == handlers
        assert sum(type(h) is logging.StreamHandler for h in logging.getLogger('ddtrace').handlers) == 1
        assert 'ddtrace.trace' not in sys.modules
    """,
        DD_INSTRUMENTATION_TELEMETRY_ENABLED=telemetry,
    )


@pytest.mark.parametrize("entrypoint", ["importlib.reload(ddtrace)", "from ddtrace.trace import tracer"])
def test_manual_import_does_not_check_python_version(entrypoint):
    run(f"""
        import importlib
        from unittest.mock import patch
        import ddtrace
        with patch('ddtrace.internal.utils.deprecations.check_supported_python_version') as check:
            {entrypoint}
            check.assert_not_called()
    """)


@pytest.mark.parametrize("entrypoint", ["import ddtrace.auto", "import ddtrace.bootstrap.sitecustomize"])
def test_bootstrap_checks_python_version_once(entrypoint):
    run(f"""
        from unittest.mock import patch
        with patch('ddtrace.internal.utils.deprecations.check_supported_python_version') as check:
            {entrypoint}
            import ddtrace.auto
            check.assert_called_once_with()
    """)


def test_ddtrace_run_checks_python_version():
    code = """
import site
from unittest.mock import patch
with patch('ddtrace.internal.utils.deprecations.check_supported_python_version') as check:
    site.main()
    check.assert_called_once_with()
from ddtrace.bootstrap import sitecustomize
assert sitecustomize.loaded
"""
    # Delay site initialization so the check can be observed before the launcher bootstrap runs.
    result = subprocess.run(["ddtrace-run", sys.executable, "-S", "-c", code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()


def test_auto_continues_when_file_logger_import_fails(tmp_path):
    result = run(
        """
        import importlib.abc
        import sys
        class BrokenFileLogger(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'logging.handlers':
                    raise ImportError('file logger unavailable')
        finder = BrokenFileLogger()
        sys.meta_path.insert(0, finder)
        import ddtrace
        sys.meta_path.remove(finder)
        import ddtrace.auto
        assert sys.modules['ddtrace.bootstrap.sitecustomize'].loaded
    """,
        DD_TRACE_LOG_FILE=str(tmp_path / "trace.log"),
    )
    assert b"Failed to configure ddtrace logging" in result.stderr
    assert b"file logger unavailable" in result.stderr
