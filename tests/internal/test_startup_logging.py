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
        assert not logging.getLogger('ddtrace').handlers
        configure_ddtrace_logger()
        assert logging.getLogger('ddtrace').handlers
        assert 'ddtrace.internal.telemetry' not in sys.modules
        assert 'ddtrace.internal.settings._config' not in sys.modules
        assert 'ddtrace.trace' not in sys.modules
    """,
        DD_INSTRUMENTATION_TELEMETRY_ENABLED=telemetry,
    )


@pytest.mark.parametrize("telemetry_first", [False, True])
def test_logging_configuration_preserves_sources(tmp_path, telemetry_first):
    local = tmp_path / "local.yaml"
    fleet = tmp_path / "fleet.yaml"
    local.write_text('apm_configuration_default:\n  DD_TRACE_LOG_LEVEL: "INFO"\n')
    fleet.write_text('config_id: "logging-config"\napm_configuration_default:\n  DD_TRACE_LOG_LEVEL: "ERROR"\n')
    run(
        f"""
        import logging
        from unittest.mock import Mock
        from ddtrace._logger import configure_ddtrace_logger
        from ddtrace.internal.settings._read_config import set_configuration_reporter
        report = Mock()
        if {telemetry_first}:
            set_configuration_reporter(report)
        configure_ddtrace_logger()
        assert logging.getLogger('ddtrace').level == logging.ERROR
        if not {telemetry_first}:
            set_configuration_reporter(report)
        records = [c.args for c in report.call_args_list if c.args[0] == 'DD_TRACE_LOG_LEVEL']
        assert records == [
            ('DD_TRACE_LOG_LEVEL', None, 'default'),
            ('DD_TRACE_LOG_LEVEL', 'WARNING', 'env_var'),
            ('DD_TRACE_LOG_LEVEL', 'ERROR', 'fleet_stable_config', 'logging-config'),
        ], records
        report.reset_mock()
        configure_ddtrace_logger()
        report.assert_not_called()
    """,
        _DD_SC_LOCAL_FILE_OVERRIDE=str(local),
        _DD_SC_MANAGED_FILE_OVERRIDE=str(fleet),
        DD_TRACE_LOG_LEVEL="WARNING",
        DD_TRACE_DEBUG="false",
    )


def test_logging_configuration_is_replayed_to_telemetry():
    run(
        """
        from unittest.mock import patch
        from ddtrace._logger import configure_ddtrace_logger
        configure_ddtrace_logger()
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.settings._read_config import _pending_configurations, record_configuration
        assert not _pending_configurations
        with patch.object(telemetry_writer, 'add_configuration') as report:
            record_configuration('DD_TRACE_LOG_LEVEL', 'INFO', 'env_var')
            report.assert_called_once_with('DD_TRACE_LOG_LEVEL', 'INFO', 'env_var')
    """,
        DD_INSTRUMENTATION_TELEMETRY_ENABLED="false",
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
def test_core_import_does_not_configure_logging(telemetry):
    run(
        """
        import sys
        import ddtrace.internal.core
        assert 'ddtrace._logger' not in sys.modules
        assert 'ddtrace.trace' not in sys.modules
    """,
        DD_INSTRUMENTATION_TELEMETRY_ENABLED=telemetry,
    )


def test_package_import_checks_python_version():
    run("""
        import importlib
        from unittest.mock import patch
        import ddtrace
        with patch('ddtrace.internal.utils.deprecations.check_supported_python_version') as check:
            importlib.reload(ddtrace)
            check.assert_called_once_with()
    """)


def test_local_logging_configuration_without_fleet_override(tmp_path):
    local = tmp_path / "local.yaml"
    local.write_text('apm_configuration_default:\n  DD_TRACE_LOG_LEVEL: "ERROR"\n')
    run(
        """
        import logging
        import os
        os.environ.pop('DD_TRACE_LOG_LEVEL', None)
        from ddtrace._logger import configure_ddtrace_logger
        configure_ddtrace_logger()
        assert logging.getLogger('ddtrace').level == logging.ERROR
        from ddtrace.internal.settings._read_config import _pending_configurations
        assert ('DD_TRACE_LOG_LEVEL', 'ERROR', 'local_stable_config') in _pending_configurations
    """,
        _DD_SC_LOCAL_FILE_OVERRIDE=str(local),
        _DD_SC_MANAGED_FILE_OVERRIDE=str(tmp_path / "missing.yaml"),
        DD_TRACE_DEBUG="false",
    )


def test_auto_continues_when_logger_import_fails():
    result = run("""
        import importlib.abc
        import sys
        class BrokenLogger(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'ddtrace._logger':
                    raise ImportError('logger unavailable')
        sys.meta_path.insert(0, BrokenLogger())
        import ddtrace.auto
        assert not sys.modules['ddtrace.bootstrap.sitecustomize'].loaded
    """)
    assert b"logger unavailable" in result.stderr
