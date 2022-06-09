import os
import re

import pytest

from ddtrace.internal.compat import PY2


LOG_PATTERN = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \w{1,} \[\S{1,}\] \[\w{1,}.\w{2}:\d{1,}\] - .{1,}$"


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_loaded_first_in_debug(
    dd_trace_debug, dd_trace_log_file_level, dd_trace_log_file, run_python_code_in_subprocess, tmpdir
):
    """
    When the tracer is imported after logging has been configured in debug mode,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    if dd_trace_debug is not None:
        env["DD_TRACE_DEBUG"] = dd_trace_debug

    if dd_trace_log_file_level is not None:
        env["DD_TRACE_LOG_FILE_LEVEL"] = dd_trace_log_file_level

    if dd_trace_log_file is not None:
        env["DD_TRACE_LOG_FILE"] = tmpdir.strpath + dd_trace_log_file
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

import ddtrace

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
ddtrace_logger.critical('ddtrace critical log')
ddtrace_logger.warning('ddtrace warning log')
"""
    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err

    if dd_trace_log_file is not None:
        assert out == b""

        if PY2 and dd_trace_debug == "true":
            assert 'No handlers could be found for logger "ddtrace' in err
        else:
            assert err == b""

        with open(tmpdir.strpath + dd_trace_log_file) as file:
            first_line = file.readline()
            assert len(first_line) > 0
            assert re.search(LOG_PATTERN, first_line) is not None
    else:
        if PY2:
            assert 'No handlers could be found for logger "ddtrace' in err
        else:
            assert b"ddtrace warning log" in err
        assert out == b""


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_loaded_last_in_debug(
    dd_trace_debug, dd_trace_log_file_level, dd_trace_log_file, run_python_code_in_subprocess, tmpdir
):
    """
    When the tracer is imported before logging has been configured in debug mode,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    if dd_trace_debug is not None:
        env["DD_TRACE_DEBUG"] = dd_trace_debug

    if dd_trace_log_file_level is not None:
        env["DD_TRACE_LOG_FILE_LEVEL"] = dd_trace_log_file_level

    if dd_trace_log_file is not None:
        env["DD_TRACE_LOG_FILE"] = tmpdir.strpath + dd_trace_log_file
    code = """
import ddtrace
import logging

custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
ddtrace_logger.critical('ddtrace critical log')
ddtrace_logger.warning('ddtrace warning log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err

    if dd_trace_log_file is not None:
        assert out == b""

        if PY2 and dd_trace_debug == "true":
            assert 'No handlers could be found for logger "ddtrace' in err
        else:
            assert err == b""

        with open(tmpdir.strpath + dd_trace_log_file) as file:
            first_line = file.readline()
            assert len(first_line) > 0
            assert re.search(LOG_PATTERN, first_line) is not None
    else:
        if PY2:
            assert 'No handlers could be found for logger "ddtrace' in err
        else:
            assert b"ddtrace warning log" in err
        assert out == b""


def test_child_logger_inherits_settings(run_python_code_in_subprocess, tmpdir):
    """
    Child loggers under the ddtrace name inherit settings as expected.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir.strpath + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 15 << 20
assert ddtrace_logger.handlers[0].backupCount == 1

child_logger = logging.getLogger('ddtrace.child')
assert child_logger.parent.name == 'ddtrace'
assert child_logger.getEffectiveLevel() == logging.DEBUG
assert child_logger.handlers == []
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err

    if PY2:
        assert 'No handlers could be found for logger "ddtrace' in err
    else:
        assert err == b""

    assert out == b""
    with open(log_file) as file:
        first_line = file.readline()
        assert len(first_line) > 0
        assert re.search(LOG_PATTERN, first_line) is not None


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_in_debug_with_ddtrace_run(
    dd_trace_debug, dd_trace_log_file_level, dd_trace_log_file, ddtrace_run_python_code_in_subprocess, tmpdir
):
    """
    When using ddtrace-run with a custom logger,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    if dd_trace_debug is not None:
        env["DD_TRACE_DEBUG"] = dd_trace_debug

    if dd_trace_log_file_level is not None:
        env["DD_TRACE_LOG_FILE_LEVEL"] = dd_trace_log_file_level

    if dd_trace_log_file is not None:
        env["DD_TRACE_LOG_FILE"] = tmpdir.strpath + dd_trace_log_file
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)
assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
ddtrace_logger = logging.getLogger('ddtrace')
ddtrace_logger.critical('ddtrace critical log')
ddtrace_logger.warning('ddtrace warning log')
"""
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert out == b""

    if dd_trace_log_file is not None:

        if dd_trace_debug == "true":
            assert "ddtrace.commands.ddtrace_run" in str(err)  # comes from ddtrace-run debug logging
        else:
            assert err == b""

        with open(tmpdir.strpath + dd_trace_log_file) as file:
            first_line = file.readline()
            assert len(first_line) > 0
            assert re.search(LOG_PATTERN, first_line) is not None

        assert out == b""

    else:
        if PY2:
            assert 'No handlers could be found for logger "ddtrace' in err
        else:
            assert b"ddtrace warning log" in err
            if dd_trace_debug == "true":
                assert "ddtrace.commands.ddtrace_run" in str(err)  # comes from ddtrace-run debug logging


def test_warn_logs_streamhandler_default(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess):
    """
    When DD_TRACE_DEBUG is not set, warn logs are emitted to StreamHandler
        following a configured logging.basicConfig and its defined format.
    """
    code = """
import logging
import ddtrace

logging.basicConfig(format='%(message)s')
ddtrace_logger = logging.getLogger('ddtrace')

assert ddtrace_logger.getEffectiveLevel() == logging.WARN
assert len(ddtrace_logger.handlers) == 0

ddtrace_logger.warning('warning log')
ddtrace_logger.debug('debug log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is None
    assert b"warning log" in err
    assert b"debug log" not in err
    assert out == b""

    code = """
import logging

logging.basicConfig(format='%(message)s')
ddtrace_logger = logging.getLogger('ddtrace')

assert ddtrace_logger.getEffectiveLevel() == logging.WARN
assert len(ddtrace_logger.handlers) == 0

ddtrace_logger.warning('warning log')
ddtrace_logger.debug('debug log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is None
    assert b"warning log" in err
    assert b"debug log" not in err
    assert out == b""


def test_warn_logs_can_go_to_file(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When DD_TRACE_DEBUG is false and DD_TRACE_LOG_FILE_LEVEL hasn't been configured,
        warn logs are emitted to the path defined in DD_TRACE_LOG_FILE.
    """
    env = os.environ.copy()
    log_file = tmpdir.strpath + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_FILE_SIZE_BYTES"] = "200000"
    code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.WARN
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 200000
assert ddtrace_logger.handlers[0].backupCount == 1

ddtrace_logger.warning('warning log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        first_line = file.readline()
        assert len(first_line) > 0
        assert "warning log" in first_line
        assert re.search(LOG_PATTERN, first_line) is not None

    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.WARN
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 200000
assert ddtrace_logger.handlers[0].backupCount == 1

ddtrace_logger.warning('warning log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        first_line = file.readline()
        assert len(first_line) > 0
        assert "warning log" in first_line
        assert re.search(LOG_PATTERN, first_line) is not None


@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
def test_debug_logs_streamhandler_default(
    dd_trace_log_file_level, run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess
):
    """
    When DD_TRACE_DEBUG is true, debug logs are emitted to StreamHandler
        following a configured logging.basicConfig and its defined format.
        Note: When running ddtrace-run, the ddtrace-run logs still emit to stderr.
        DD_TRACE_LOG_FILE_LEVEL does not affect this setting.
    """
    env = os.environ.copy()
    if dd_trace_log_file_level is not None:
        env["DD_TRACE_LOG_FILE_LEVEL"] = dd_trace_log_file_level
    env["DD_TRACE_DEBUG"] = "true"
    code = """
import logging
import ddtrace

logging.basicConfig(format='%(message)s')
ddtrace_logger = logging.getLogger('ddtrace')

assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 0

ddtrace_logger.warning('warning log')
ddtrace_logger.debug('debug log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is None
    assert b"warning log" in err
    assert b"debug log" in err
    assert out == b""

    code = """
import logging

logging.basicConfig(format='%(message)s')
ddtrace_logger = logging.getLogger('ddtrace')

assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 0

ddtrace_logger.warning('warning log')
ddtrace_logger.debug('debug log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is None
    assert "program executable" in str(err)  # comes from ddtrace-run debug logging
    assert b"warning log" in err
    assert b"debug log" in err
    assert out == b""


@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
def test_debug_logs_can_go_to_file_backup_count(
    dd_trace_log_file_level, run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir
):
    """
    When DD_TRACE_DEBUG is true and DD_TRACE_LOG_FILE has been specified, debug logs are
        written to a file at the expected backup count, based on the DD_TRACE_LOG_FILE_LEVEL setting.
        Note: When running ddtrace-run, the ddtrace-run logs still emit to stderr.
    """
    env = os.environ.copy()
    if dd_trace_log_file_level is not None:
        env["DD_TRACE_LOG_FILE_LEVEL"] = dd_trace_log_file_level

    log_file = tmpdir.strpath + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_FILE_SIZE_BYTES"] = "10"
    code = """
import logging
import os
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 10
assert ddtrace_logger.handlers[0].backupCount == 1
if os.environ.get("DD_TRACE_LOG_FILE_LEVEL") is not None:
    ddtrace_logger.handlers[0].level == getattr(logging, os.environ.get("DD_TRACE_LOG_FILE_LEVEL"))

ddtrace_logger = logging.getLogger('ddtrace')

for attempt in range(100):
    ddtrace_logger.debug('ddtrace multiple debug log')
    ddtrace_logger.critical('ddtrace multiple debug log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err

    if PY2:
        assert 'No handlers could be found for logger "ddtrace' in err
    else:
        assert err == b""

    assert out == b""

    testfiles = os.listdir(tmpdir.strpath)
    log_files = [filename for filename in testfiles if "testlog.log" in filename]
    log_files.sort()
    assert log_files == ["testlog.log", "testlog.log.1"]

    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None

    code = """
import logging
import os

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 10
assert ddtrace_logger.handlers[0].backupCount == 1

if os.environ.get("DD_TRACE_LOG_FILE_LEVEL") is not None:
    ddtrace_logger.handlers[0].level == getattr(logging, os.environ.get("DD_TRACE_LOG_FILE_LEVEL"))

for attempt in range(100):
    ddtrace_logger.debug('ddtrace multiple debug log')
    ddtrace_logger.critical('ddtrace multiple debug log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err

    if PY2:
        assert 'No handlers could be found for logger "ddtrace' in err
    else:
        assert "program executable" in str(err)  # comes from ddtrace-run debug logging

    assert out == b""

    testfiles = os.listdir(tmpdir.strpath)
    log_files = [filename for filename in testfiles if "testlog.log" in filename]
    log_files.sort()
    assert log_files == ["testlog.log", "testlog.log.1"]

    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None


def test_unknown_log_level_error(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When DD_TRACE_LOG_FILE_LEVEL is set to an unknown env var, the application raises an error and no logs are written.
    """
    env = os.environ.copy()
    env["DD_TRACE_LOG_FILE_LEVEL"] = "UNKNOWN"
    log_file = tmpdir.strpath + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_FILE_SIZE_BYTES"] = "10"
    code = """
import logging
import ddtrace
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 1, err
    assert "ValueError" in str(err)
    assert out == b""

    testfiles = os.listdir(tmpdir.strpath)
    log_files = [filename for filename in testfiles if "testlog.log" in filename]
    assert log_files == []

    code = """
import logging
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 1, err
    assert "ValueError" in str(err)
    assert out == b""

    testfiles = os.listdir(tmpdir.strpath)
    log_files = [filename for filename in testfiles if "testlog.log" in filename]
    assert log_files == []
