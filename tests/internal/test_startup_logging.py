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
        import ddtrace
        assert len(logging.getLogger('ddtrace').handlers) == 1
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
        assert logging.getLogger('ddtrace').level == {"logging.WARNING" if level else "logging.NOTSET"}
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


def test_bootstrap_uses_full_file_logging_configuration():
    run("""
        from unittest.mock import patch
        from ddtrace.internal.telemetry import get_config
        with patch('ddtrace._logger._configure_ddtrace_file_logger') as configure:
            import ddtrace.auto
            configure.assert_called_once_with(get_config)
    """)


def test_native_logging_waits_for_configuration(tmp_path):
    logfile = tmp_path / "native.log"
    run(
        f"""
        import sys
        from pathlib import Path
        from unittest.mock import patch
        import ddtrace
        assert 'ddtrace.internal.native' not in sys.modules
        from ddtrace.internal import native
        assert 'ddtrace.internal.telemetry' not in sys.modules
        assert not Path({str(logfile)!r}).exists()
        configure = native._configure_ddtrace_native_logger
        with patch.object(native, '_configure_ddtrace_native_logger', wraps=configure) as setup:
            from ddtrace.internal.telemetry import get_config
            assert 'ddtrace.trace' not in sys.modules
            import ddtrace.auto
            setup.assert_called_once_with(get_config)
        native.logger.log('warning', 'native logging enabled')
        native.logger.disable('file')
        assert 'native logging enabled' in Path({str(logfile)!r}).read_text()
    """,
        _DD_NATIVE_LOGGING_BACKEND="file",
        _DD_NATIVE_LOGGING_FILE_PATH=str(logfile),
    )


@pytest.mark.parametrize(
    "initial, replacement", [(False, None), (False, "new"), (True, None), (True, "same"), (True, "new")]
)
def test_file_logging_replaces_previous_handler(tmp_path, initial, replacement):
    old_file = tmp_path / "old.log"
    new_file = old_file if replacement == "same" else tmp_path / "new.log"
    destination = str(new_file) if replacement else None
    run(
        f"""
        import logging
        import os
        if not {initial}:
            os.environ.pop('DD_TRACE_LOG_FILE', None)
        from ddtrace._logger import _configure_ddtrace_file_logger
        logger = logging.getLogger('ddtrace')
        unrelated = logging.NullHandler()
        unrelated.set_name('application-handler')
        logger.addHandler(unrelated)
        old_handlers = [h for h in logger.handlers if h.name == 'ddtrace_file']
        values = {{'DD_TRACE_LOG_FILE': {destination!r}, 'DD_TRACE_LOG_FILE_LEVEL': 'ERROR',
                   'DD_TRACE_LOG_FILE_SIZE_BYTES': '2048'}}
        def retrieve(name, default=None, modifier=None):
            value = values.get(name, default)
            return modifier(value) if modifier else value
        logger.warning('before-reconfiguration')
        for _ in range(2):
            _configure_ddtrace_file_logger(retrieve)
        handlers = [h for h in logger.handlers if h.name == 'ddtrace_file']
        assert len(handlers) == {int(destination is not None)}
        assert unrelated in logger.handlers
        assert all(h.stream is None for h in old_handlers)
        if handlers:
            assert handlers[0].level == logging.ERROR
            assert handlers[0].maxBytes == 2048
            assert handlers[0].baseFilename == {destination!r}
        logger.warning('filtered-file-message')
        logger.error('after-reconfiguration')
    """,
        DD_TRACE_DEBUG="false",
        DD_TRACE_LOG_LEVEL="WARNING",
        DD_TRACE_LOG_FILE=str(old_file),
    )
    contents = new_file.read_text() if new_file.exists() else ""
    assert contents.count("after-reconfiguration") == int(destination is not None)
    assert "filtered-file-message" not in contents
    if old_file != new_file and old_file.exists():
        assert "after-reconfiguration" not in old_file.read_text()


def test_invalid_file_reconfiguration_keeps_existing_handler(tmp_path):
    logfile = tmp_path / "trace.log"
    result = run(
        f"""
        import logging
        from ddtrace._logger import _configure_ddtrace_file_logger
        from ddtrace.internal import telemetry
        from unittest.mock import patch
        logger = logging.getLogger('ddtrace')
        original = next(h for h in logger.handlers if h.name == 'ddtrace_file')
        with patch.dict(telemetry.FLEET_CONFIG, {{'DD_TRACE_LOG_FILE': {str(tmp_path / "missing" / "bad.log")!r}}}):
            import ddtrace.auto
        from ddtrace.bootstrap import sitecustomize
        assert sitecustomize.loaded
        assert original in logger.handlers
        assert not original.stream.closed
        logger.warning('logging still works')
    """,
        DD_TRACE_LOG_FILE=str(logfile),
    )
    assert b"Failed to configure ddtrace logging" in result.stderr
    assert logfile.read_text().count("logging still works") == 1


def test_bootstrap_file_logging_honors_fleet_and_reports_telemetry(tmp_path):
    old_file = tmp_path / "env.log"
    new_file = tmp_path / "fleet.log"
    run(
        f"""
        import logging
        from unittest.mock import patch
        from ddtrace.internal import telemetry
        with patch.dict(telemetry.FLEET_CONFIG, {{'DD_TRACE_LOG_FILE': {str(new_file)!r}}}, clear=True):
            with patch.object(telemetry.telemetry_writer, 'add_configuration') as report:
                import ddtrace.auto
                report.assert_any_call('DD_TRACE_LOG_FILE', {str(new_file)!r}, 'fleet_stable_config', None)
        logging.getLogger('ddtrace').warning('resolved-file-output')
    """,
        DD_TRACE_DEBUG="false",
        DD_TRACE_LOG_FILE=str(old_file),
    )
    assert new_file.read_text().count("resolved-file-output") == 1
    assert "resolved-file-output" not in old_file.read_text()


def test_native_logging_uses_resolved_configuration():
    result = run(
        """
        from unittest.mock import patch
        from ddtrace.internal.settings import _core
        with patch.dict(_core.FLEET_CONFIG, {'_DD_NATIVE_LOGGING_BACKEND': 'stdout'}, clear=True):
            import ddtrace.internal.telemetry
        from ddtrace.internal.native import logger
        logger.log('warning', 'resolved-native-output')
        logger.disable('stdout')
    """,
        DD_TRACE_DEBUG="false",
        _DD_NATIVE_LOGGING_BACKEND="stderr",
    )
    assert result.stdout.count(b"resolved-native-output") == 1
    assert b"resolved-native-output" not in result.stderr


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
        from unittest.mock import patch
        import ddtrace
        handlers = list(logging.getLogger('ddtrace').handlers)
        assert len(handlers) == 2, handlers
        with patch('ddtrace._logger.configure_ddtrace_logger') as configure:
            {entrypoint}
            configure.assert_not_called()
        current = logging.getLogger('ddtrace').handlers
        assert sum(type(h) is logging.StreamHandler for h in current) == 1
        assert handlers[0] in current
        assert sum(h.name == 'ddtrace_file' for h in current) == 1
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
        import logging
        assert any(type(h) is logging.StreamHandler for h in logging.getLogger('ddtrace').handlers)
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
