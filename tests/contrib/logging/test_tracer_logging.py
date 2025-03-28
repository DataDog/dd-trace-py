import os
import re

import pytest


# example: '2022-06-10 21:49:26,010 CRITICAL [ddtrace] [test.py:15] - ddtrace critical log\n'
LOG_PATTERN = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \w{1,} \[\S{1,}\] \[\w{1,}.\w{2}:\d{1,}\] - .{1,}$"


def assert_log_files(test_directory, test_log_file, total_file_count):
    """
    helper for asserting different log file counts.
    test_directory: directory with the test files
    test_log_file: file that is being asserted
    total_file_count: total files in the directory, including backup logs.
    """
    testfiles = os.listdir(test_directory)
    log_files = [filename for filename in testfiles if test_log_file in filename]
    if total_file_count > 0:
        log_files.sort()

        backup_log_files = [test_log_file + "." + str(i) for i in range(1, total_file_count)]

        assert log_files == [test_log_file] + backup_log_files

        assert_file_contains_log(test_directory + "/" + test_log_file)

    else:
        assert log_files == []


def assert_file_logging(expected_log, out, err, dd_trace_debug, dd_log_path):
    if dd_log_path is not None:
        assert out == b""

        assert_file_contains_log(dd_log_path)
    else:
        assert expected_log in err
        assert out == b""


def assert_file_contains_log(dd_log_path):
    with open(dd_log_path) as file:
        first_line = file.readline()
        assert len(first_line) > 0
        assert re.search(LOG_PATTERN, first_line) is not None


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_loaded_first(
    dd_trace_debug, dd_trace_log_file_level, dd_trace_log_file, run_python_code_in_subprocess, tmpdir
):
    """
    When the tracer is imported after logging has been configured,
    the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    if dd_trace_debug is not None:
        env["DD_TRACE_DEBUG"] = dd_trace_debug

    if dd_trace_log_file_level is not None:
        env["DD_TRACE_LOG_FILE_LEVEL"] = dd_trace_log_file_level

    ddtrace_log_path = None
    if dd_trace_log_file is not None:
        ddtrace_log_path = tmpdir.strpath + "/" + dd_trace_log_file
        env["DD_TRACE_LOG_FILE"] = ddtrace_log_path
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
"""
    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert_file_logging(b"ddtrace critical log", out, err, dd_trace_debug, ddtrace_log_path)


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file_level", ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_loaded_last(
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

    ddtrace_log_path = None
    if dd_trace_log_file is not None:
        ddtrace_log_path = tmpdir.strpath + "/" + dd_trace_log_file
        env["DD_TRACE_LOG_FILE"] = tmpdir.strpath + "/" + dd_trace_log_file
    code = """
import ddtrace
import logging

custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
ddtrace_logger.critical('ddtrace critical log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err

    assert_file_logging(b"ddtrace critical log", out, err, dd_trace_debug, ddtrace_log_path)


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
        env["DD_TRACE_LOG_FILE"] = tmpdir.strpath + "/" + dd_trace_log_file
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

        assert_file_contains_log(tmpdir.strpath + "/" + dd_trace_log_file)

        assert out == b""

    else:
        assert b"ddtrace warning log" in err
        if dd_trace_debug == "true":
            assert "ddtrace.commands.ddtrace_run" in str(err)  # comes from ddtrace-run debug logging


def test_logs_with_basicConfig(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess):
    """
    When DD_TRACE_DEBUG is not set and logging.basicConfig() is set, then logs are emitted
    to stderr.
    """
    for run_in_subprocess in [run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess]:
        code = """
import logging
import ddtrace

logging.basicConfig(format='%(message)s')
ddtrace_logger = logging.getLogger('ddtrace')

assert ddtrace_logger.getEffectiveLevel() == logging.WARN

ddtrace_logger.warning('warning log')
ddtrace_logger.debug('debug log')
"""

        out, err, status, pid = run_in_subprocess(code)
        assert status == 0, err
        assert re.search(LOG_PATTERN, str(err)) is None
        assert b"warning log" in err, err.decode()
        assert b"debug log" not in err, err.decode()
        assert out == b""


def test_warn_logs_can_go_to_file(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When DD_TRACE_DEBUG is false and DD_TRACE_LOG_FILE_LEVEL hasn't been configured,
    warn logs are emitted to the path defined in DD_TRACE_LOG_FILE.
    """
    env = os.environ.copy()
    log_file = tmpdir.strpath + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_LOG_FILE_SIZE_BYTES"] = "200000"
    patch_code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.WARN
assert len(ddtrace_logger.handlers) == 3
assert isinstance(ddtrace_logger.handlers[2], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[2].maxBytes == 200000
assert ddtrace_logger.handlers[2].backupCount == 1

ddtrace_logger.warning('warning log')
"""

    ddtrace_run_code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.WARN
assert len(ddtrace_logger.handlers) == 3
assert isinstance(ddtrace_logger.handlers[2], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[2].maxBytes == 200000
assert ddtrace_logger.handlers[2].backupCount == 1

ddtrace_logger.warning('warning log')
"""

    for run_in_subprocess, code in [
        (run_python_code_in_subprocess, patch_code),
        (ddtrace_run_python_code_in_subprocess, ddtrace_run_code),
    ]:
        out, err, status, pid = run_in_subprocess(code, env=env)
        assert status == 0, err
        assert err == b"warning log\n", err.decode()
        assert out == b"", out.decode()
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

ddtrace_logger.warning('warning log')
ddtrace_logger.debug('debug log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is None
    assert "program executable" in str(err)  # comes from ddtrace-run debug logging
    assert b"warning log" in err, err.decode()
    assert b"debug log" in err, err.decode()
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
    env["DD_TRACE_LOG_FILE_SIZE_BYTES"] = "10"
    code = """
import logging
import os
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 3
assert isinstance(ddtrace_logger.handlers[2], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[2].maxBytes == 10
assert ddtrace_logger.handlers[2].backupCount == 1
if os.environ.get("DD_TRACE_LOG_FILE_LEVEL") is not None:
    ddtrace_logger.handlers[2].level == getattr(logging, os.environ.get("DD_TRACE_LOG_FILE_LEVEL"))

ddtrace_logger = logging.getLogger('ddtrace')

for attempt in range(100):
    ddtrace_logger.debug('ddtrace multiple debug log')
    ddtrace_logger.critical('ddtrace multiple debug log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)

    assert status == 0, err

    assert out == b""

    assert_log_files(tmpdir.strpath, "testlog.log", 2)

    code = """
import logging
import os

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 3
assert isinstance(ddtrace_logger.handlers[2], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[2].maxBytes == 10
assert ddtrace_logger.handlers[2].backupCount == 1

if os.environ.get("DD_TRACE_LOG_FILE_LEVEL") is not None:
    ddtrace_logger.handlers[2].level == getattr(logging, os.environ.get("DD_TRACE_LOG_FILE_LEVEL"))

for attempt in range(100):
    ddtrace_logger.debug('ddtrace multiple debug log')
    ddtrace_logger.critical('ddtrace multiple debug log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err.decode()

    assert "program executable" in str(err)  # comes from ddtrace-run debug logging

    assert out == b""

    assert_log_files(tmpdir.strpath, "testlog.log", 2)


def test_unknown_log_level_error(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When DD_TRACE_LOG_FILE_LEVEL is set to an unknown env var, the application raises an error and no logs are written.
    """
    env = os.environ.copy()
    env["DD_TRACE_LOG_FILE_LEVEL"] = "UNKNOWN"
    log_file = tmpdir.strpath + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_LOG_FILE_SIZE_BYTES"] = "10"
    code = """
import logging
import ddtrace
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 1, err
    assert "ValueError" in str(err)
    assert out == b""

    assert_log_files(tmpdir.strpath, "testlog.log", 0)

    code = """
import logging
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 1, err
    assert "ValueError" in str(err)
    assert out == b""

    assert_log_files(tmpdir.strpath, "testlog.log", 0)
