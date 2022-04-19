import os
import re

import pytest


LOG_PATTERN = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \w{1,} \[\S{1,}\] \[\w{1,}.\w{2}:\d{1,}\] - .{1,}$"


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_loaded_first_in_debug(dd_trace_debug, dd_trace_log_file, run_python_code_in_subprocess):
    """
    When the tracer is imported after logging has been configured in debug mode,
        the ddtrace logger does not override any custom logs settings.
        If the root logger has not been configured, debug tracer logs are not sent anywhere.
    """
    env = os.environ.copy()
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

import ddtrace

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_loaded_last_in_debug(dd_trace_debug, dd_trace_log_file, run_python_code_in_subprocess):
    """
    When the tracer is imported before logging has been configured in debug mode,
        the ddtrace logger does not override any custom logs settings.
        If the root logger has not been configured, debug tracer logs are not sent anywhere.
    """
    env = os.environ.copy()
    code = """
import ddtrace
import logging

custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""


def test_child_logger_inherits_settings(run_python_code_in_subprocess, tmpdir):
    """
    Child loggers under the ddtrace name inherit settings as expected.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir + "/testlog.log"
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
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None


@pytest.mark.parametrize("dd_trace_debug", ["true", "false", None])
@pytest.mark.parametrize("dd_trace_log_file", ["example.log", None])
def test_unrelated_logger_in_debug_with_ddtrace_run(
    dd_trace_debug, dd_trace_log_file, ddtrace_run_python_code_in_subprocess, tmpdir
):
    """
    When using ddtrace-run with a custom logger,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

custom_logger_formatter = logging.Formatter('%(message)s')
custom_logger_handler = logging.StreamHandler()
custom_logger_handler.setFormatter(custom_logger_formatter)
custom_logger.addHandler(custom_logger_handler)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""


def test_warn_logs_streamhandler_default(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess):
    """
    When DD_TRACE_DEBUG is false, warn logs are emitted to StreamHandler
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
    When DD_TRACE_DEBUG is false, warn logs are emitted to the path defined in DD_TRACE_LOG_FILE.
    """
    env = os.environ.copy()
    log_file = tmpdir + "/testlog.log"
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
        content = file.read()
        assert len(content) > 0
        assert "warning log" in content
        assert re.search(LOG_PATTERN, content) is not None

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
        content = file.read()
        assert len(content) > 0
        assert "warning log" in content
        assert re.search(LOG_PATTERN, content) is not None


def test_debug_logs_streamhandler_default(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess):
    """
    When DD_TRACE_DEBUG is true, debug logs are emitted to StreamHandler
        following a configured logging.basicConfig and its defined format.
        Note: When running ddtrace-run, the ddtrace-run logs still emit to stderr.
    """
    env = os.environ.copy()
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
    assert "program executable" in str(err)
    assert b"warning log" in err
    assert b"debug log" in err
    assert out == b""


def test_debug_logs_can_go_to_file_backup_count(
    run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir
):
    """
    When DD_TRACE_DEBUG is true and DD_TRACE_LOG_FILE has been specified, debug logs are
        written to a file at the expected backup count.
        Note: When running ddtrace-run, the ddtrace-run logs still emit to stderr.
    """
    env = os.environ.copy()
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_FILE_SIZE_BYTES"] = "10"
    code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 10
assert ddtrace_logger.handlers[0].backupCount == 1
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""

    testfiles = os.listdir(tmpdir)
    log_files = [
        filename for filename in testfiles if "testlog.log" in filename
    ]

    assert log_files == [
        "testlog.log",
        "testlog.log.1"
    ]

    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None

    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.getEffectiveLevel() == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 10
assert ddtrace_logger.handlers[0].backupCount == 1
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert "program executable" in str(err)
    assert out == b""

    testfiles = os.listdir(tmpdir)
    log_files = [
        filename for filename in testfiles if "testlog.log" in filename
    ]

    assert log_files == [
        "testlog.log",
        "testlog.log.1"
    ]

    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None
