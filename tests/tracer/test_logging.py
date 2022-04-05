import os
import re


LOG_PATTERN = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \w{1,} \[\S{1,}\] \[\w{1,}.\w{2}:\d{1,}\] {}- .{1,}"


def test_unrelated_logger_loaded_first_in_debug(run_python_code_in_subprocess):
    """
    When the tracer is imported after logging has been configured in debug mode,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
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
    assert err != b""
    assert out == b""


def test_unrelated_logger_loaded_last_in_debug(run_python_code_in_subprocess):
    """
    When the tracer is imported before logging has been configured in debug mode,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
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
    assert err != b""
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

def test_unrelated_logger_in_debug_with_ddtrace_run(ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When using ddtrace-run with a custom logger,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_FILE_SIZE_BYTES"] = "200000"
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None


def test_unrelated_logger_in_warn_with_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    """
    When using ddtrace-run and a custom logger without debug mode enabled,
        the ddtrace logger does not override any custom logs settings.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "false"
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.StreamHandler)

ddtrace_logger.warning('warning log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is not None
    assert b"warning log" in err
    assert out == b""


def test_warn_logs_go_to_stderr_default(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess):
    """
    When setting up the default ddtrace logger when not in debug mode,
        it automatically logs to stderr at WARN log level.
    """
    code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.StreamHandler)

ddtrace_logger.warning('warning log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is not None
    assert b"warning log" in err
    assert out == b""

    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.StreamHandler)

ddtrace_logger.warning('warning log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is not None
    assert b"warning log" in err
    assert out == b""

def test_warn_logs_can_go_to_file(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When setting up the default ddtrace logger when not in debug mode,
        it can log to a file at WARN log level.
    """
    env = os.environ.copy()
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    env["DD_TRACE_FILE_SIZE_BYTES"] = "200000"
    code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
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
assert ddtrace_logger.level == logging.WARN
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

def test_debug_logs_go_to_stderr(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess):
    """
    When setting up the default logger in debug mode without a log path,
        it automatically logs to stderr.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    code = """
import logging
import ddtrace

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.StreamHandler)
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is not None
    assert b"debug logs" in err
    assert out == b""

    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.StreamHandler)
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert re.search(LOG_PATTERN, str(err)) is not None
    assert b"debug logs" in err
    assert out == b""

def test_debug_logs_can_go_to_file(run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When setting up the default ddtrace logger in debug mode,
        it automatically logs to a file at the expected backup count.
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
assert ddtrace_logger.level == logging.DEBUG
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
    files_created = 0
    for file in testfiles:
        if 'testlog.log' in file: files_created = files_created + 1

    assert files_created == 2
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None

    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert len(ddtrace_logger.handlers) == 1
assert isinstance(ddtrace_logger.handlers[0], logging.handlers.RotatingFileHandler)
assert ddtrace_logger.handlers[0].maxBytes == 10
assert ddtrace_logger.handlers[0].backupCount == 1
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""

    testfiles = os.listdir(tmpdir)
    files_created = 0
    for file in testfiles:
        if 'testlog.log' in file: files_created = files_created + 1

    assert files_created == 2
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0
        assert re.search(LOG_PATTERN, content) is not None
