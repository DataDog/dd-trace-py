import os


def test_unrelated_logger_in_debug_with_patch_all(run_python_code_in_subprocess):
    """
    When importing the tracer after the custom log,
                the ddtrace logger does not override any custom logs settings
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_LOG_FILE"] = "./testlog.log"
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

import ddtrace

ddtrace.patch_all()

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


def test_unrelated_logger_loaded_last_patch_all(run_python_code_in_subprocess):
    """
    When importing the tracer before the custom log,
                the ddtrace logger does not override any custom logs settings
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_LOG_FILE"] = "./testlog.log"
    code = """
import ddtrace
import logging

ddtrace.patch_all()

custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


def test_child_logger_inherits_settings_patch_all(run_python_code_in_subprocess):
    """
    When setting up a child logger under ddtrace, it inherits from ddtrace automatically.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_LOG_FILE"] = "./testlog.log"
    code = """
import logging
from ddtrace.logger import configure_ddtrace_logger
import ddtrace

ddtrace.patch_all()

child_logger = logging.getLogger('ddtrace.child')
assert child_logger.parent.name == 'ddtrace'
assert child_logger.parent.getEffectiveLevel() == child_logger.getEffectiveLevel()
assert child_logger.getEffectiveLevel() == logging.DEBUG
assert child_logger.handlers == []
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


def test_debug_logs_go_to_stderr_patch_all(run_python_code_in_subprocess):
    """
    When setting up the default logger, it automatically logs to stderr.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    code = """
import logging
from ddtrace.logger import configure_ddtrace_logger
import ddtrace

ddtrace.patch_all()

s = ddtrace.tracer.trace("custom_span", resource="resource_name")

s.finish()
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err != b""


def test_debug_logs_go_to_stderr_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    """
    When setting up the default logger with ddtrace-run, it automatically logs to stderr.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"

    code = """
import logging
from ddtrace.logger import configure_ddtrace_logger
import ddtrace

s = ddtrace.tracer.trace("custom_span", resource="resource_name")

s.finish()
"""
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err != b""


def test_unrelated_logger_in_debug_with_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    """
    When using ddtrace-run and a custom logger with debug mode enabled,
                the ddtrace logger does not override any custom logs settings and
                child loggers continue to inherit from the parent.
                Debug logs from the python tracer are logged.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

child_logger = logging.getLogger('ddtrace.child')
assert child_logger.parent.name == 'ddtrace'
assert child_logger.parent.getEffectiveLevel() == child_logger.getEffectiveLevel()
assert child_logger.getEffectiveLevel() == logging.DEBUG
assert child_logger.handlers == []

"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert str(err).find("DEBUG [ddtrace] [logger.py") != -1


def test_unrelated_logger_in_no_debug_with_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    """
    When using ddtrace-run and a custom logger without debug mode enabled,
                the ddtrace logger does not override any custom logs settings and
                child loggers continue to inherit from the parent.
                No debug logs from the tracer are created.
                The custom logger logs continue to be emitted.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "false"
    code = """
import logging
import ddtrace
custom_logger = logging.getLogger('custom')
stream_handler = logging.StreamHandler()
custom_logger.addHandler(stream_handler)
custom_logger.setLevel(logging.INFO)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.INFO

child_logger = logging.getLogger('ddtrace.child')
assert child_logger.parent.name == 'ddtrace'
assert child_logger.parent.getEffectiveLevel() == child_logger.getEffectiveLevel()
assert child_logger.getEffectiveLevel() == logging.WARN
assert child_logger.handlers == []

s = ddtrace.tracer.trace("custom_span", resource="resource_name")
s.set_tag(None)
s.finish()

custom_logger.info('custom info log')
custom_logger.debug('custom debug log')
custom_logger.warn('custom warn log')

"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert str(err).find("WARNING [ddtrace.span]") != -1
    assert str(err).find("DEBUG [ddtrace] [logger.py") == -1
    assert str(err).find("custom debug log") == -1
    assert str(err).find("custom warn log") != -1
    assert str(err).find("custom info log") != -1
