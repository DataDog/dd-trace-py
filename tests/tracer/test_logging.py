import os


def test_unrelated_logger_in_debug_with_patch_all(run_python_code_in_subprocess, tmpdir):
    """
    When importing the tracer after logging has been configured,
                with debug mode and enabling a custom log path,
                the ddtrace logger does not override any custom logs settings and
                the ddtrace logger continues to have its expected configuration and
                logs to a custom file path.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
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

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert ddtrace_logger.handlers[0].__class__.__name__ == 'RotatingFileHandler'
assert ddtrace_logger.handlers[0].maxBytes == 15 << 20
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0


def test_unrelated_logger_loaded_last_patch_all(run_python_code_in_subprocess, tmpdir):
    """
    When importing the tracer after logging has been configured,
                with debug mode and enabling a custom log path,
                the ddtrace logger does not override any custom logs settings and
                the ddtrace logger continues to have its expected configuration and
                logs to a custom file path.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    code = """
import ddtrace
import logging

ddtrace.patch_all()

custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert ddtrace_logger.handlers[0].__class__.__name__ == 'RotatingFileHandler'
assert ddtrace_logger.handlers[0].maxBytes == 15 << 20
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0


def test_child_logger_inherits_settings_patch_all(run_python_code_in_subprocess, tmpdir):
    """
    When setting up a child logger under ddtrace with debug mode and log path enabled,
        the child logger inherits from ddtrace automatically.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    code = """
import logging
import ddtrace

ddtrace.patch_all()

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert ddtrace_logger.handlers[0].__class__.__name__ == 'RotatingFileHandler'
assert ddtrace_logger.handlers[0].maxBytes == 15 << 20

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


def test_debug_logs_go_to_stderr_patch_all(run_python_code_in_subprocess):
    """
    When setting up the default logger in debug mode,
        it automatically logs to stderr.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    code = """
import logging
import ddtrace

ddtrace.patch_all()

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert ddtrace_logger.handlers[0].__class__.__name__ == 'StreamHandler'
"""

    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err != b""
    assert out == b""


def test_warn_logs_go_to_stderr_patch_all(run_python_code_in_subprocess):
    """
    When setting up the default logger,
        it automatically logs to stderr at WARN log level.
    """
    code = """
import logging
import ddtrace

ddtrace.patch_all()

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
assert ddtrace_logger.handlers[0].__class__.__name__ == 'StreamHandler'

ddtrace_logger.warning('warning log')
"""

    out, err, status, pid = run_python_code_in_subprocess(code)
    assert status == 0, err
    assert err != b""
    assert out == b""


def test_debug_logs_go_to_stderr_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    """
    When setting up the default logger with ddtrace-run in debug mode,
        it automatically logs to stderr.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"

    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert ddtrace_logger.handlers[0].__class__.__name__ == 'StreamHandler'
"""
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err != b""
    assert out == b""


def test_warn_logs_go_to_stderr_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    """
    When setting up the default logger with ddtrace-run without debug mode,
        it automatically logs to stderr.
    """
    code = """
import logging

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
assert ddtrace_logger.handlers[0].__class__.__name__ == 'StreamHandler'

ddtrace_logger.warning('warning log')
"""
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, err
    assert err != b""
    assert out == b""


def test_unrelated_logger_in_debug_with_ddtrace_run(ddtrace_run_python_code_in_subprocess, tmpdir):
    """
    When using ddtrace-run after logging has been configured,
                with debug mode and enabling a custom log path,
                the ddtrace logger does not override any custom logs settings and
                the ddtrace logger continues to have its expected configuration and
                logs to a custom file path.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    log_file = tmpdir + "/testlog.log"
    env["DD_TRACE_LOG_FILE"] = log_file
    code = """
import logging
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.DEBUG
assert ddtrace_logger.handlers[0].__class__.__name__ == 'RotatingFileHandler'
assert ddtrace_logger.handlers[0].maxBytes == 15 << 20

"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""
    assert out == b""
    with open(log_file) as file:
        content = file.read()
        assert len(content) > 0


def test_unrelated_logger_in_warn_with_ddtrace_run(ddtrace_run_python_code_in_subprocess):
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
custom_logger = logging.getLogger('custom')
custom_logger.setLevel(logging.WARN)

assert custom_logger.parent.name == 'root'
assert custom_logger.level == logging.WARN

ddtrace_logger = logging.getLogger('ddtrace')
assert ddtrace_logger.level == logging.WARN
assert ddtrace_logger.handlers[0].__class__.__name__ == 'StreamHandler'

ddtrace_logger.warning('warning log')

child_logger = logging.getLogger('ddtrace.child')
assert child_logger.parent.name == 'ddtrace'
assert child_logger.getEffectiveLevel() == logging.WARN
assert child_logger.handlers == []

child_logger.warning('warning log')
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err != b""
    assert out == b""
